import { createHash } from "node:crypto";
import { createInterface } from "node:readline";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile, appendFile, lstat, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  AssistantMessageEventStream,
} from "../upstream/pi/packages/ai/dist/utils/event-stream.js";
import {
  createAgentSession,
  createExtensionRuntime,
  ModelRuntime,
  SessionManager,
  SettingsManager,
} from "../upstream/pi/packages/coding-agent/dist/index.js";
import { createControlledTools, sanitizeWire, serializeContext } from "./agent.mjs";

const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TRACE_SCHEMA = "goal-native-pi-session-trace/v1";
const RESULT_SCHEMA = "goal-native-pi-session-result/v1";
const RPC_SCHEMA = "goal-native-controller-rpc/v1";
const MAX_ROUNDS = 100;
const MAX_SECONDS = 3600;

function jsonLine(value) {
  return JSON.stringify(value, (_key, item) => {
    if (typeof item === "bigint") return Number(item);
    if (typeof item === "function") return undefined;
    return item;
  });
}

function writeLine(value) {
  process.stdout.write(`${jsonLine(value)}\n`);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function safeError(error) {
  const text = error instanceof Error ? error.message : String(error);
  if (/(oauth|codex|refresh[_\s-]?token|access[_\s-]?token|credential|api[_\s-]?key|bearer)/i.test(text)) {
    return "provider authentication failed";
  }
  return text.replace(/Bearer\s+\S+/gi, "Bearer [redacted]").slice(0, 1000);
}

function requiredString(value, name) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${name} is required`);
  return value;
}

function requiredHash(value, name) {
  const hash = requiredString(value, name);
  if (!/^[0-9a-f]{64}$/.test(hash)) throw new Error(`${name} must be a lowercase SHA-256`);
  return hash;
}

function absolutePath(value, name) {
  const supplied = requiredString(value, name);
  if (!path.isAbsolute(supplied)) throw new Error(`${name} must be absolute`);
  return path.resolve(supplied);
}

async function canonicalPath(value, name, { rejectLeafSymlink = false } = {}) {
  const resolved = absolutePath(value, name);
  let leaf;
  try {
    leaf = await lstat(resolved);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  if (leaf?.isSymbolicLink()) {
    if (rejectLeafSymlink) throw new Error(`${name} must not be a symlink`);
    try {
      await realpath(resolved);
    } catch (error) {
      throw new Error(`${name} must not be a dangling symlink`);
    }
  }
  let current = resolved;
  const missing = [];
  while (true) {
    try {
      const details = await lstat(current);
      if (missing.length) {
        let followed;
        try {
          followed = await stat(current);
        } catch (error) {
          if (details.isSymbolicLink() && error?.code === "ENOENT") {
            throw new Error(`${name} contains a dangling symlink component`);
          }
          throw error;
        }
        if (!followed.isDirectory()) {
          throw new Error(`${name} contains a non-directory path component`);
        }
      }
      const canonical = await realpath(current);
      return path.join(canonical, ...missing.reverse());
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      const parent = path.dirname(current);
      if (parent === current) throw new Error(`${name} has no existing parent`);
      missing.push(path.basename(current));
      current = parent;
    }
  }
}

async function directoryPath(value, name) {
  const canonical = await canonicalPath(value, name);
  try {
    const details = await lstat(canonical);
    if (!details.isDirectory()) throw new Error(`${name} must be a directory`);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  return canonical;
}

async function regularFilePath(value, name) {
  const canonical = await canonicalPath(value, name, { rejectLeafSymlink: true });
  let details;
  try {
    details = await lstat(canonical);
  } catch (error) {
    throw new Error(`${name} must identify a regular explicit file`);
  }
  if (!details.isFile()) throw new Error(`${name} must identify a regular explicit file`);
  return canonical;
}

async function outputFilePath(value, name) {
  const canonical = await canonicalPath(value, name, { rejectLeafSymlink: true });
  try {
    const details = await lstat(canonical);
    if (!details.isFile()) throw new Error(`${name} must identify a regular file path`);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  return canonical;
}

async function rejectSymlinkComponents(value, name) {
  const resolved = absolutePath(value, name);
  const parsed = path.parse(resolved);
  let current = parsed.root;
  const components = resolved.slice(parsed.root.length).split(path.sep).filter(Boolean);
  for (const component of components) {
    current = path.join(current, component);
    let details;
    try {
      details = await lstat(current);
    } catch (error) {
      if (error?.code === "ENOENT") throw new Error(`${name} is missing`);
      throw error;
    }
    if (details.isSymbolicLink()) throw new Error(`${name} must not contain symlink components`);
    if (current !== resolved && !details.isDirectory()) {
      throw new Error(`${name} contains a non-directory path component`);
    }
  }
  return resolved;
}

async function sessionFilePath(value, sessionDirectory, name) {
  const raw = requiredString(value, name);
  if (!path.isAbsolute(raw)) throw new Error(`${name} must be absolute`);
  const resolved = await rejectSymlinkComponents(raw, name);
  let details;
  try {
    details = await lstat(resolved);
  } catch (error) {
    throw new Error(`${name} must identify an existing regular file`);
  }
  if (!details.isFile()) throw new Error(`${name} must identify an existing regular file`);
  const canonical = await realpath(resolved);
  if (!outside(canonical, sessionDirectory)) {
    throw new Error(`${name} must be within the authorized session_dir`);
  }
  return canonical;
}

function outside(candidate, parent) {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate));
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

function requireOutside(candidate, parent, name) {
  if (outside(candidate, parent)) throw new Error(`${name} must be outside ${parent}`);
  return candidate;
}

async function requireDirectory(value, name) {
  const resolved = absolutePath(value, name);
  const details = await lstat(resolved);
  if (!details.isDirectory()) throw new Error(`${name} must be a real directory`);
  return await realpath(resolved);
}

function resourceLoader() {
  // Explicitly empty: native comparison sessions must not discover ambient
  // project/user extensions, skills, prompts, themes, or context files.
  const extensions = { extensions: [], errors: [], runtime: createExtensionRuntime() };
  return {
    getExtensions: () => extensions,
    getSkills: () => ({ skills: [], diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => undefined,
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [],
    getAppendSystemPromptSources: () => [],
    extendResources: () => {},
    reload: async () => {},
  };
}

function modelSummary(model) {
  return {
    provider: model?.provider,
    id: model?.id,
    api: model?.api,
    contextWindow: model?.contextWindow,
    maxTokens: model?.maxTokens,
  };
}

function sessionKey(request) {
  const key = requiredString(request.session_key, "session_key");
  if (!/^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(key)) throw new Error("session_key must be an explicit safe identifier");
  return key;
}

async function loadOrCreateSession(request, cwd, sessionDirectory) {
  const key = sessionKey(request);
  const manifestPath = path.join(sessionDirectory, `${key}.json`);
  let manifestDetails = null;
  try {
    manifestDetails = await lstat(manifestPath);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  if (manifestDetails?.isSymbolicLink()) throw new Error("persisted native session manifest must not be a symlink");
  if (request.phase === "cold") {
    if (manifestDetails) throw new Error(`persisted native session already exists for ${key}`);
    await mkdir(sessionDirectory, { recursive: true });
    const manager = SessionManager.create(cwd, sessionDirectory);
    // A fresh SessionManager has chosen its path but has not written a file yet.
    const sessionFile = await outputFilePath(manager.getSessionFile(), "native session file");
    if (!outside(sessionFile, sessionDirectory)) {
      throw new Error("native session file must be within the authorized session_dir");
    }
    const continuationRef = `pi-session-v1:${manager.getSessionId()}`;
    await writeFile(manifestPath, `${jsonLine({
      key,
      continuation_ref: continuationRef,
      session_id: manager.getSessionId(),
      session_file: sessionFile,
      task_id: request.task_id ?? null,
      registration_sha256: request.registration_sha256 ?? null,
      task_snapshot_sha256: request.task_snapshot_sha256 ?? null,
      candidate_identity: request.candidate_identity ?? null,
      check_identity: request.check_identity ?? null,
      environment_identity: request.environment_identity ?? null,
      environment_snapshot_sha256: request.environment_snapshot_sha256 ?? null,
    })}\n`, { encoding: "utf8", flag: "wx" });
    return { manager, continuationRef, resumedFrom: null, manifestPath, sessionFile };
  }
  if (request.phase !== "continuation") throw new Error("phase must be cold or continuation");
  if (!manifestDetails?.isFile()) throw new Error("persisted native session manifest must be a regular file");
  const suppliedRef = requiredString(request.continuation_ref, "continuation_ref");
  let manifest;
  try {
    manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  } catch (error) {
    throw new Error(`cannot open persisted native session manifest: ${safeError(error)}`);
  }
  if (!manifest || manifest.continuation_ref !== suppliedRef || typeof manifest.session_file !== "string"
      || manifest.task_id !== (request.task_id ?? null)
      || manifest.registration_sha256 !== (request.registration_sha256 ?? null)
      || manifest.task_snapshot_sha256 !== (request.task_snapshot_sha256 ?? null)
      || manifest.candidate_identity !== (request.candidate_identity ?? null)
      || manifest.check_identity !== (request.check_identity ?? null)
      || manifest.environment_identity !== (request.environment_identity ?? null)
      || manifest.environment_snapshot_sha256 !== (request.environment_snapshot_sha256 ?? null)) {
    throw new Error("continuation_ref or persisted native session identity does not match this request");
  }
  const sessionFile = await sessionFilePath(manifest.session_file, sessionDirectory, "manifest.session_file");
  const manager = SessionManager.open(sessionFile, sessionDirectory, cwd);
  if (manager.getSessionId() !== manifest.session_id) throw new Error("persisted native session identity changed");
  return { manager, continuationRef: suppliedRef, resumedFrom: suppliedRef, manifestPath, sessionFile };
}

function toolNamesFor(request, tools) {
  const names = tools.map((tool) => tool.name);
  if (new Set(names).size !== names.length) throw new Error("controller tool loadout contains duplicate names");
  if (names.includes("staged_command") && (request.command_runtime === false || request.command_runtime_available === false)) {
    throw new Error("staged_command is unavailable without the controller command runtime");
  }
  if (Array.isArray(request.available_tools)) {
    const allowed = new Set(request.available_tools.map((name) => requiredString(name, "available_tools item")));
    if (names.some((name) => !allowed.has(name)) || names.length !== allowed.size) {
      throw new Error("native tool loadout differs from the controller-supplied available tools");
    }
  }
  return names;
}

function governedRuntime(runtime, rpc, metadata, emitEvent, flushEvents, beforeRequest) {
  return new Proxy(runtime, {
    get(target, property) {
      if (property === "streamSimple") {
        return (model, context, options = {}) => {
          const stream = new AssistantMessageEventStream();
          (async () => {
            try {
              await flushEvents();
              metadata.requestAdmitted = false;
              beforeRequest();
              const admission = await rpc("prepare_request", {
                schema: RPC_SCHEMA,
                run_id: metadata.runId,
                invocation_id: metadata.invocationId,
                model: modelSummary(model),
                context: serializeContext(context),
                context_digest: sha256(canonical(context)),
                tool_profile: metadata.tool_profile,
                safety_profile: metadata.safety_profile,
                candidate_identity: metadata.candidate_identity,
                registration_sha256: metadata.registration_sha256,
                task_snapshot_sha256: metadata.task_snapshot_sha256,
                check_identity: metadata.check_identity,
                environment_identity: metadata.environment_identity,
                environment_snapshot_sha256: metadata.environment_snapshot_sha256,
              });
              if (!admission || admission.ok !== true || typeof admission.invocation_id !== "string") {
                const denied = new Error(admission?.error || "provider request was not admitted by the controller");
                denied.status = admission?.status || "failed";
                throw denied;
              }
              metadata.invocationId = admission.invocation_id;
              metadata.requestAdmitted = true;
              const providerOptions = {
                ...options,
                maxRetries: 0,
                maxRetryDelayMs: 0,
                transport: "sse",
                onPayload: async (payload, payloadModel) => {
                  metadata.providerPayloads += 1;
                  await rpc("provider_payload", {
                    run_id: metadata.runId,
                    invocation_id: metadata.invocationId,
                    model: modelSummary(payloadModel),
                    payload: JSON.parse(jsonLine(payload)),
                  });
                },
                onProviderStreamEvent: async (data, eventModel) => emitEvent({
                  type: "provider_stream_event",
                  data: JSON.parse(jsonLine(data)),
                  model: modelSummary(eventModel),
                }),
                onResponse: async (response, responseModel) => emitEvent({
                  type: "provider_response",
                  response: JSON.parse(jsonLine(response)),
                  model: modelSummary(responseModel),
                }),
              };
              const inner = target.streamSimple(model, context, providerOptions);
              for await (const event of inner) stream.push(event);
              stream.end(await inner.result());
            } catch (error) {
              if (!metadata.requestAdmitted) {
                metadata.admissionRejection = {
                  status: error?.status === "interrupted" ? "interrupted" : "failed",
                  stop_reason: "controller_admission",
                };
              }
              const message = {
                role: "assistant",
                content: [],
                api: model.api,
                provider: model.provider,
                model: model.id,
                usage: {
                  input: 0,
                  output: 0,
                  cacheRead: 0,
                  cacheWrite: 0,
                  totalTokens: 0,
                  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
                },
                stopReason: "error",
                errorMessage: safeError(error),
                timestamp: Date.now(),
              };
              stream.push({ type: "error", reason: "error", error: message });
              stream.end(message);
            }
          })();
          return stream;
        };
      }
      const value = Reflect.get(target, property, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

function collectUsage(events, expectedResponses) {
  // SDK error messages contain synthetic zero counters. Only provider-reported
  // response usage is evidence; an incomplete request keeps totals unknown.
  const responses = new Map();
  for (const event of events) {
    const response = event?.type === "provider_stream_event" ? event.data?.response : null;
    if (typeof response?.id === "string") responses.set(response.id, response.usage);
  }
  const sum = (select) => {
    if (!responses.size || responses.size !== expectedResponses) return null;
    let total = 0;
    for (const usage of responses.values()) {
      const value = select(usage);
      if (!Number.isSafeInteger(value) || value < 0) return null;
      total += value;
    }
    return total;
  };
  return {
    input_tokens: sum((usage) => usage?.input_tokens),
    output_tokens: sum((usage) => usage?.output_tokens),
    cached_input_tokens: sum((usage) => usage?.input_tokens_details?.cached_tokens),
    total_tokens: sum((usage) => usage?.total_tokens),
    cost_usd: null,
  };
}

function assistantText(events) {
  const messages = events.filter((event) => event?.type === "message_end" && event.message?.role === "assistant");
  const last = messages.at(-1)?.message;
  if (!last || !Array.isArray(last.content)) return "";
  return last.content.filter((part) => part?.type === "text").map((part) => part.text).filter((text) => typeof text === "string").join("\n");
}

function providerRequestId(events) {
  for (const event of events) {
    const response = event?.type === "provider_stream_event" ? event.data?.response : null;
    if (typeof response?.id === "string") return response.id;
    const message = event?.message ?? event?.assistantMessageEvent?.message;
    if (event?.type === "message_end" && message?.role === "assistant" && typeof message.responseId === "string" && message.responseId) {
      return message.responseId;
    }
  }
  return null;
}

function resultStatus(events, cancellation, admissionRejection) {
  if (cancellation.requested) return { status: "cancelled", stop_reason: cancellation.reason || "cancelled" };
  if (cancellation.reason === "round_limit" || cancellation.reason === "time_limit") return { status: "interrupted", stop_reason: cancellation.reason };
  if (admissionRejection) return admissionRejection;
  const errors = events.filter((event) => event?.type === "error" || (event?.type === "message_end" && event.message?.role === "assistant" && event.message.stopReason === "error"));
  if (errors.length) return { status: "failed", stop_reason: "provider_error" };
  return { status: "completed", stop_reason: "completed" };
}


async function runRequest(request, rpc, emit, control = {}) {
  const started = performance.now();
  const phase = requiredString(request.phase, "phase");
  const provider = requiredString(request.provider, "provider");
  if (provider !== "openai-codex") throw new Error("native-pi comparison only accepts the explicit openai-codex subscription provider; there is no billing fallback");
  const modelId = requiredString(request.model, "model");
  const toolProfile = requiredString(request.tool_profile, "tool_profile");
  const safetyProfile = requiredString(request.safety_profile, "safety_profile");
  const repositoryRoot = await realpath(REPOSITORY_ROOT);
  const cwd = requireOutside(await requireDirectory(request.cwd, "cwd"), repositoryRoot, "cwd");
  const stateDirectory = requireOutside(await directoryPath(request.state_dir, "state_dir"), repositoryRoot, "state_dir");
  const sessionDirectory = requireOutside(
    requireOutside(await directoryPath(request.session_dir, "session_dir"), cwd, "session_dir"),
    repositoryRoot,
    "session_dir",
  );
  const authFile = requireOutside(await regularFilePath(request.auth_file, "auth_file"), repositoryRoot, "auth_file");
  const rawTracePath = requireOutside(
    requireOutside(await outputFilePath(request.raw_trace_path, "raw_trace_path"), cwd, "raw_trace_path"),
    repositoryRoot,
    "raw_trace_path",
  );
  const maxRounds = Number.isInteger(request.max_rounds) ? request.max_rounds : 12;
  const maxSeconds = Number.isFinite(request.max_time_seconds) ? request.max_time_seconds : 300;
  if (maxRounds < 1 || maxRounds > MAX_ROUNDS) throw new Error(`max_rounds must be between 1 and ${MAX_ROUNDS}`);
  if (maxSeconds <= 0 || maxSeconds > MAX_SECONDS) throw new Error(`max_time_seconds must be between 0 and ${MAX_SECONDS}`);
  if (request.max_cost_usd != null) throw new Error("subscription billing cost is unreported; use explicit round and time limits");
  const maxTraceEvents = Number.isInteger(request.max_trace_events) ? request.max_trace_events : 10000;
  const maxTraceBytes = Number.isInteger(request.max_trace_bytes) ? request.max_trace_bytes : 50_000_000;
  if (maxTraceEvents < 1 || maxTraceEvents > 100000) throw new Error("max_trace_events is outside the supported bound");
  if (maxTraceBytes < 1 || maxTraceBytes > 500_000_000) throw new Error("max_trace_bytes is outside the supported bound");
  const prompt = requiredString(request.prompt, "prompt");
  const registrationHash = requiredHash(request.registration_sha256, "registration_sha256");
  const taskSnapshot = requiredString(request.task_snapshot_sha256, "task_snapshot_sha256");
  const candidateIdentity = requiredString(request.candidate_identity, "candidate_identity");
  const checkIdentity = requiredString(request.check_identity, "check_identity");
  const environmentIdentity = requiredString(request.environment_identity, "environment_identity");
  const environmentSnapshot = requiredHash(request.environment_snapshot_sha256, "environment_snapshot_sha256");
  const recordEvent = async (event, forward) => {
    if (traceHandle.events.length >= maxTraceEvents) throw new Error("native trace event bound exceeded");
    const safe = sanitizeWire(JSON.parse(jsonLine(event)));
    const line = `${jsonLine(safe)}\n`;
    const lineBytes = Buffer.byteLength(line);
    if (traceHandle.bytes + lineBytes > maxTraceBytes) throw new Error("native trace byte bound exceeded");
    traceHandle.hash.update(line);
    traceHandle.events.push(safe);
    traceHandle.bytes += lineBytes;
    await appendFile(rawTracePath, line, "utf8");
    if (forward) {
      await emit(["provider_stream_event", "provider_response"].includes(safe.type)
        ? safe : { type: "agent_event", event: safe });
    }
  };
  const enqueueEvent = (event, forward = true) => {
    const next = eventQueue.then(() => recordEvent(event, forward));
    eventQueue = next.catch((error) => {
      traceError = error;
      control.cancel?.("trace_limit");
    });
    return next;
  };
  if (!Array.isArray(request.tools)) throw new Error("controller request.tools must be the exported tool_schemas profile");
  if (request.tool_schemas !== undefined) throw new Error("tool_schemas is not the controller request field; use exported request.tools");
  const toolSchemas = request.tools;
  const metadata = {
    runId: requiredString(request.run_id, "run_id"),
    invocationId: request.invocation_id ?? null,
    requestAdmitted: false,
    providerPayloads: 0,
    tool_profile: toolProfile,
    safety_profile: safetyProfile,
    candidate_identity: candidateIdentity,
    registration_sha256: registrationHash,
    task_snapshot_sha256: taskSnapshot,
    check_identity: checkIdentity,
    environment_identity: environmentIdentity,
    environment_snapshot_sha256: environmentSnapshot,
  };
  const controllerTools = createControlledTools(rpc, metadata, toolSchemas);
  const toolNames = toolNamesFor(request, controllerTools);
  const settingsManager = SettingsManager.inMemory({
    retry: { enabled: false, maxRetries: 0, provider: { maxRetries: 0, maxRetryDelayMs: 0 } },
    defaultProjectTrust: "never",
    compaction: { enabled: false },
    cacheWarming: "off",
  });
  process.env.PI_OFFLINE = "1";
  const modelRuntime = await ModelRuntime.create({ authPath: authFile, modelsPath: null, allowModelNetwork: false, refreshOnCreate: false });
  const model = modelRuntime.getModel(provider, modelId);
  if (!model) throw new Error(`pinned pi catalog does not contain ${provider}/${modelId}`);
  const sessionState = await loadOrCreateSession(request, cwd, sessionDirectory);
  await mkdir(stateDirectory, { recursive: true });
  await mkdir(path.dirname(rawTracePath), { recursive: true });
  if (existsSync(rawTracePath)) throw new Error("raw_trace_path already exists; native traces are immutable");
  await writeFile(rawTracePath, "", { encoding: "utf8", flag: "wx" });
  const traceHandle = { hash: createHash("sha256"), events: [], bytes: 0 };
  let traceError = null;
  let eventQueue = Promise.resolve();
  const cancellation = { requested: false, reason: null };
  let rounds = 0;
  const governed = governedRuntime(modelRuntime, rpc, metadata, enqueueEvent, () => eventQueue, () => {
    if (rounds >= maxRounds) {
      cancellation.reason = "round_limit";
      throw new Error("native provider request round limit reached");
    }
    rounds += 1;
  });
  const { session } = await createAgentSession({
    cwd,
    agentDir: stateDirectory,
    modelRuntime: governed,
    model,
    thinkingLevel: request.thinking_level || "off",
    tools: toolNames,
    noTools: "builtin",
    customTools: controllerTools,
    resourceLoader: resourceLoader(),
    sessionManager: sessionState.manager,
    settingsManager,
  });
  const activeToolNames = session.getActiveToolNames();
  if (activeToolNames.length !== toolNames.length || activeToolNames.some((name) => !toolNames.includes(name))) {
    session.dispose();
    throw new Error("native session activated a different controller tool profile");
  }
  control.cancel = (reason = "cancelled") => {
    cancellation.requested = true;
    cancellation.reason = reason;
    void session.abort();
  };
  const unsubscribe = session.subscribe((event) => {
    // A rejected request can make the SDK emit local error/lifecycle messages.
    // Retain those in its trace, never attach them to an unadmitted invocation.
    enqueueEvent(event, metadata.requestAdmitted);
  });
  const timer = setTimeout(() => {
    if (!cancellation.requested) {
      cancellation.reason = "time_limit";
      void session.abort();
    }
  }, Math.ceil(maxSeconds * 1000));
  try {
    if (control.cancelled) control.cancel("controller_closed");
    else await session.prompt(prompt, { expandPromptTemplates: false, source: "rpc" });
    await session.waitForIdle();
  } finally {
    clearTimeout(timer);
    unsubscribe();
    await eventQueue;
    session.dispose();
  }
  const transcriptSha256 = sha256(await readFile(sessionState.sessionFile));
  if (traceError) throw new Error(`native trace capture failed: ${safeError(traceError)}`);
  const usage = collectUsage(traceHandle.events, metadata.providerPayloads);
  const status = resultStatus(traceHandle.events, cancellation, metadata.admissionRejection);
  const traceSha256 = traceHandle.hash.digest("hex");
  const requestId = providerRequestId(traceHandle.events);
  return {
    protocol: RESULT_SCHEMA,
    status: status.status,
    stop_reason: status.stop_reason,
    provider: {
      kind: "pi-coding-agent-sdk",
      evidence: "createAgentSession/SessionManager persistent native session",
      request_id: requestId,
      model: modelId,
      native_session: true,
      transcript_imported: false,
      session_id: sessionState.manager.getSessionId(),
      session_file: sessionState.sessionFile,
      transcript_sha256: transcriptSha256,
    },
    configuration: {
      provider,
      max_rounds: maxRounds,
      max_time_seconds: maxSeconds,
      tool_profile: toolProfile,
      safety_profile: safetyProfile,
      tool_names: activeToolNames,
      tool_schema_sha256: sha256(canonical(request.tools)),
      extensions: "none",
    },
    identity: {
      registration_sha256: registrationHash,
      task_snapshot_sha256: taskSnapshot,
      candidate_identity: candidateIdentity,
      check_identity: checkIdentity,
      environment_identity: environmentIdentity,
      environment_snapshot_sha256: environmentSnapshot,
    },
    lifecycle: {
      phase,
      continuation_ref: sessionState.continuationRef,
      resumed_from: sessionState.resumedFrom,
      session_id: sessionState.manager.getSessionId(),
    },
    usage,
    metrics: { latency_ms: Math.round((performance.now() - started) * 1000) / 1000, rounds },
    raw_trace: { schema: TRACE_SCHEMA, path: rawTracePath, sha256: traceSha256, event_count: traceHandle.events.length, bytes: traceHandle.bytes },
    observations: { assistant_text: assistantText(traceHandle.events) },
  };
}

function rpcClient() {
  const pending = new Map();
  let sequence = 0;
  const request = (method, payload) => new Promise((resolve, reject) => {
    const id = `controller-rpc-${++sequence}`;
    pending.set(id, { resolve, reject });
    writeLine({ type: "rpc", id, method, payload });
  });
  return {
    request,
    resolve(message) {
      const waiting = pending.get(message.id);
      if (!waiting) return;
      pending.delete(message.id);
      if (message.ok === true) waiting.resolve(message.result);
      else waiting.reject(Object.assign(new Error(message.error || "controller RPC failed"), { status: message.status || "failed" }));
    },
    rejectAll(error) {
      for (const waiting of pending.values()) waiting.reject(error);
      pending.clear();
    },
  };
}

function startProcess() {
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const controller = rpcClient();
  let active = null;
  const start = async (message) => {
    if (active) {
      writeLine({ type: "result", request_id: message.request_id, ok: false, status: "failed", error: "native pi adapter is already running" });
      return;
    }
    active = { cancelled: false, session: null, cancel: null };
    try {
      const request = message.request ?? message;
      const result = await runRequest(request, (method, payload) => controller.request(method, payload), async (event) => writeLine({ type: "event", event }), active);
      writeLine({ type: "result", request_id: message.request_id, ok: true, result });
    } catch (error) {
      writeLine({ type: "result", request_id: message.request_id, ok: false, status: error?.status || "failed", error: safeError(error) });
    } finally {
      active = null;
    }
  };
  input.on("line", (line) => {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      writeLine({ type: "error", error: "invalid JSON command" });
      return;
    }
    if (message?.type === "rpc_result") {
      controller.resolve(message);
      return;
    }
    if (message?.type === "cancel") {
      if (active) {
        active.cancelled = true;
        active.cancel?.("cancelled");
      }
      return;
    }
    if (message?.type === "run" || message?.protocol === "goal-native-pi-request/v1") {
      void start(message);
      return;
    }
    writeLine({ type: "error", error: "unknown command" });
  });
  input.on("close", () => {
    if (active) active.cancelled = true;
    active?.cancel?.("controller_closed");
    controller.rejectAll(new Error("controller input closed"));
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) startProcess();
