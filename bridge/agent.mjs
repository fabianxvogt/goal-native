import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import { Agent } from "../upstream/pi/packages/agent/dist/index.js";
import { AuthStorage } from "../upstream/pi/packages/coding-agent/dist/core/auth-storage.js";
import { Type, createModels } from "../upstream/pi/packages/ai/dist/index.js";
import { openaiCodexProvider } from "../upstream/pi/packages/ai/dist/providers/openai-codex.js";
import { openaiProvider } from "../upstream/pi/packages/ai/dist/providers/openai.js";
import { declarationsEqual, getCurrentTools } from "../upstream/pi/packages/ai/dist/utils/transcript.js";


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

function modelSummary(model) {
  return {
    provider: model.provider,
    id: model.id,
    api: model.api,
    contextWindow: model.contextWindow,
    maxTokens: model.maxTokens,
  };
}

function normalizeMessage(message) {
  if (!message || typeof message !== "object") {
    throw new Error("initial message must be an object");
  }
  const timestamp = Number.isInteger(message.timestamp) ? message.timestamp : Date.now();
  if (message.role === "developer") {
    return { role: "system", content: String(message.content ?? ""), timestamp };
  }
  if (["system", "user", "assistant", "toolResult"].includes(message.role)) {
    return { ...message, timestamp };
  }
  throw new Error(`unsupported initial message role: ${message.role}`);
}

function serializableTools(tools) {
  return tools.map(({ name, label, description, parameters }) => ({
    name,
    label,
    description,
    parameters,
  }));
}

export function serializeContext(context) {
  const messages = (context.messages ?? []).map((message) => {
    if (message.role !== "toolResult") return jsonSafe(message);
    // These fields belong to UI/tool accounting, not the provider's input.
    const { details, usage, ...modelMessage } = message;
    return jsonSafe(modelMessage);
  });
  const declared = new Map(getCurrentTools(messages).map((tool) => [tool.name, tool]));
  const tools = serializableTools(context.tools ?? []).filter((tool) => {
    const prior = declared.get(tool.name);
    return !prior || !declarationsEqual(prior, tool);
  });
  return { messages, tools };
}

function jsonSafe(value) {
  return JSON.parse(jsonLine(value));
}
const SENSITIVE_KEYS = /^(access|refresh|access_token|refresh_token|apiKey|authorization|cookie|secret|password|credential)$/i;

function redactText(value) {
  let text = String(value)
    .replace(/Bearer\s+[A-Za-z0-9._~+/-]+/gi, "Bearer [redacted]")
    .replace(/(access|refresh)_token[=:]\s*[^\s,&}]+/gi, "$1_token=[redacted]")
    .replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "[redacted-token]");
  const apiKey = process.env.OPENAI_API_KEY;
  if (apiKey && apiKey.length >= 8) text = text.split(apiKey).join("[redacted]");
  return text;
}

export function sanitizeWire(value) {
  if (Array.isArray(value)) return value.map(sanitizeWire);
  if (!value || typeof value !== "object") return typeof value === "string" ? redactText(value) : value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    SENSITIVE_KEYS.test(key) ? "[redacted]" : sanitizeWire(item),
  ]));
}

function safeRuntimeError(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (/(oauth|codex|refresh[_\s-]?token|access[_\s-]?token|credential|api[_\s-]?key|bearer)/i.test(message)) {
    return "provider authentication failed";
  }
  return redactText(message).slice(0, 1000);
}


function toolDefinition(name, label, description, parameters, rpc, metadata) {
  return {
    name,
    label,
    description,
    parameters,
    executionMode: "sequential",
    execute: async (toolCallId, params, signal) => {
      if (signal?.aborted) throw new Error("tool execution aborted");
      const result = await rpc("tool_call", {
        run_id: metadata.runId,
        invocation_id: metadata.invocationId,
        tool_call_id: toolCallId,
        name,
        arguments: params,
      });
      if (!result || result.ok !== true) {
        throw new Error(result?.error || `controlled tool ${name} failed`);
      }
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
        details: result,
      };
    },
  };
}

export function createControlledTools(rpc, metadata, schemas) {
  if (!Array.isArray(schemas) || schemas.length === 0 || schemas.length > 32) {
    throw new Error("controller tool schemas are required");
  }
  const names = new Set();
  return schemas.map(({ name, description, parameters }) => {
    if (typeof name !== "string" || !/^[a-z_]+$/.test(name) || names.has(name)
        || typeof description !== "string" || parameters?.type !== "object") {
      throw new Error("invalid or duplicate controller tool schema");
    }
    names.add(name);
    return toolDefinition(name, name.replaceAll("_", " "), description,
      Type.Unsafe(parameters), rpc, metadata);
  });
}
function resolveModel(modelId, providerId, authFile) {
  if (typeof modelId !== "string" || !modelId.trim()) {
    throw new Error("an explicit model id is required");
  }
  if (providerId !== "openai-codex" && providerId !== "openai") {
    throw new Error("an explicit provider is required: openai-codex or openai");
  }

  if (providerId === "openai-codex") {
    if (typeof authFile !== "string" || !authFile.trim()) {
      throw new Error("auth_file is required for provider openai-codex");
    }
    if (process.env.OPENAI_BASE_URL) {
      throw new Error("OPENAI_BASE_URL is not permitted for provider openai-codex");
    }
    const credentials = AuthStorage.create(authFile);
    const models = createModels({ credentials });
    models.setProvider(openaiCodexProvider());
    const model = models.getModel(providerId, modelId);
    if (!model) throw new Error(`OpenAI Codex model is not available in pi catalog: ${modelId}`);
    const authPreflight = async () => {
      const entry = (await credentials.list({ signal: new AbortController().signal }))
        .find((candidate) => candidate.providerId === providerId);
      if (entry && entry.type !== "oauth") {
        throw new Error("OpenAI Codex requires an OAuth credential");
      }
    };
    return { model, models, authPreflight };
  }

  const models = createModels();
  models.setProvider(openaiProvider());
  const model = models.getModel(providerId, modelId);
  if (!model) throw new Error(`OpenAI model is not available in pi catalog: ${modelId}`);
  const endpoint = process.env.OPENAI_BASE_URL;
  if (endpoint) {
    const parsed = new URL(endpoint);
    if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname))) {
      throw new Error("OPENAI_BASE_URL must use HTTPS or loopback HTTP");
    }
  }
  return { model: endpoint ? { ...model, baseUrl: endpoint } : model, models };
}

export function createAgent({
  provider,
  authFile,
  modelId,
  messages,
  toolSchemas,
  runId,
  invocationId,
  maxRounds = 12,
  maxOutputTokens,
  rpc,
  emit = async () => {},
  streamFn,
  model: suppliedModel,
  models: suppliedModels,
}) {
  if (typeof rpc !== "function") throw new Error("pi bridge RPC function is required");
  const resolved = suppliedModel ? { model: suppliedModel, models: suppliedModels } : resolveModel(modelId, provider, authFile);
  const model = resolved.model;
  const metadata = { runId, invocationId };
  const tools = createControlledTools(rpc, metadata, toolSchemas);
  const toolNames = new Set(tools.map((tool) => tool.name));
  let rounds = 0;
  const baseStream = streamFn || ((requestModel, context, options = {}) => (
    resolved.models.streamSimple(requestModel, context, options)
  ));
  const providerStream = (requestModel, context, options = {}) => {
    const providerOptions = {
      ...options,
      maxRetries: 0,
      maxRetryDelayMs: 0,
      ...(requestModel.provider === "openai-codex" ? { transport: "sse" } : {}),
      ...(requestModel.provider !== "openai-codex" && Number.isInteger(maxOutputTokens) ? { maxTokens: maxOutputTokens } : {}),
    };
    if (!streamFn) {
      providerOptions.onPayload = async (payload, payloadModel) => {
        await rpc("provider_payload", {
          run_id: metadata.runId,
          invocation_id: metadata.invocationId,
          model: modelSummary(payloadModel),
          payload: jsonSafe(payload),
        });
      };
      providerOptions.onProviderStreamEvent = async (data, eventModel) => {
        await emit({
          type: "provider_stream_event",
          data: jsonSafe(data),
          model: modelSummary(eventModel),
        });
      };
      providerOptions.onResponse = async (response, responseModel) => {
        await emit({
          type: "provider_response",
          response: jsonSafe(response),
          model: modelSummary(responseModel),
        });
      };
    }
    return baseStream(requestModel, context, providerOptions);
  };
  const agent = new Agent({
    initialState: {
      model,
      messages: (messages ?? []).map(normalizeMessage),
      tools,
    },
    streamFn: providerStream,
    toolExecution: "sequential",
    maxRetryDelayMs: 0,
    beforeToolCall: async ({ toolCall }) => {
      if (!toolNames.has(toolCall.name)) {
        return { block: true, reason: `tool is not in the controlled loadout: ${toolCall.name}`, terminate: true };
      }
      return undefined;
    },
    prepareRequest: async ({ context, model: requestModel }) => {
      const response = await rpc("prepare_request", {
        run_id: runId,
        invocation_id: metadata.invocationId,
        model: modelSummary(requestModel),
        context: serializeContext(context),
      });
      if (!response || response.ok !== true || typeof response.invocation_id !== "string") {
        const error = new Error(response?.error || "provider request was not admitted");
        error.status = response?.status || "failed";
        throw error;
      }
      metadata.invocationId = response.invocation_id;
      return { context };
    },
    finishTurn: async ({ message }) => {
      rounds += 1;
      if (rounds >= maxRounds) return { action: "end" };
      if (message.stopReason === "error" || message.stopReason === "aborted") return undefined;
      return undefined;
    },
  });
  return { agent, model, rounds: () => rounds, invocationId: () => metadata.invocationId, authPreflight: resolved.authPreflight };
}

function messageText(message) {
  if (!message || message.role !== "assistant" || !Array.isArray(message.content)) return "";
  return message.content
    .filter((part) => part?.type === "text")
    .map((part) => part.text)
    .filter((text) => typeof text === "string")
    .join("\n");
}

export async function runAgent(options) {
  const { agent, rounds, invocationId, authPreflight } = createAgent(options);
  options.onAgent?.(agent);
  await options.emit?.({
    type: "agent_config",
    tools: agent.state.tools.map((tool) => tool.name),
    model: modelSummary(agent.state.model),
    provider_output_limit_supported: agent.state.model.provider !== "openai-codex",
  });
  agent.subscribe(async (event) => {
    await options.emit?.({ type: "agent_event", event: sanitizeWire(jsonSafe(event)) });
  });
  let status = "finished";
  let error;
  let stopReason = "completed";
  try {
    await authPreflight?.();
    await agent.continue();
    await agent.waitForIdle();
    if (agent.state.errorMessage) {
      status = options.cancelled?.() ? "cancelled" : "failed";
      stopReason = options.cancelled?.() ? "cancelled" : "agent_error";
      error = safeRuntimeError(agent.state.errorMessage);
    }
  } catch (caught) {
    status = options.cancelled?.() ? "cancelled" : (caught?.status || "failed");
    stopReason = options.cancelled?.() ? "cancelled" : (caught?.status === "interrupted" ? "provider_interrupt" : "provider_error");
    error = safeRuntimeError(caught);
  }
  const lastAssistant = [...agent.state.messages].reverse().find((message) => message?.role === "assistant");
  const resultText = messageText(lastAssistant);
  if (status === "finished" && rounds() >= options.maxRounds && lastAssistant?.stopReason === "toolUse") {
    status = "interrupted";
    stopReason = "round_limit";
    error = "pi agent round budget exhausted before completion";
  }
  return {
    status,
    stop_reason: stopReason,
    invocation_id: invocationId(),
    rounds: rounds(),
    result: resultText,
    ...(error ? { error } : {}),
  };
}

function startProcess() {
  const pending = new Map();
  let sequence = 0;
  let active = null;
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const rpc = (method, payload) => new Promise((resolve, reject) => {
    const id = `rpc-${++sequence}`;
    pending.set(id, { resolve, reject });
    writeLine({ type: "rpc", id, method, payload });
  });
  const failPending = (error) => {
    for (const { reject } of pending.values()) reject(error);
    pending.clear();
  };
  const startRun = async (request) => {
    const run = {
      request,
      cancelled: false,
      emit: async (event) => writeLine({ type: "event", event: sanitizeWire(event) }),
    };
    active = run;
    try {
      const result = await runAgent({
        provider: request.provider,
        authFile: request.auth_file,
        modelId: request.model,
        messages: request.messages,
        toolSchemas: request.tools,
        runId: request.run_id,
        invocationId: request.invocation_id,
        maxRounds: request.max_rounds,
        maxOutputTokens: request.max_output_tokens,
        rpc,
        emit: run.emit,
        onAgent: (agent) => { run.agent = agent; },
        cancelled: () => run.cancelled,
      });
      writeLine({ type: "result", request_id: request.request_id, ok: true, result });
    } catch (error) {
      writeLine({
        type: "result",
        request_id: request.request_id,
        ok: false,
        status: error?.status || "failed",
        error: safeRuntimeError(error),
      });
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
      const waiting = pending.get(message.id);
      if (!waiting) return;
      pending.delete(message.id);
      if (message.ok === true) waiting.resolve(message.result);
      else {
        const error = new Error(message.error || "Python RPC failed");
        error.status = message.status || "failed";
        waiting.reject(error);
      }
      return;
    }
    if (message?.type === "cancel") {
      if (active) {
        active.cancelled = true;
        active.agent?.abort();
      }
      return;
    }
    if (message?.type === "run") {
      if (active) {
        writeLine({ type: "result", request_id: message.request_id, ok: false, status: "failed", error: "pi bridge is already running" });
        return;
      }
      void startRun(message);
      return;
    }
    writeLine({ type: "error", error: "unknown command" });
  });
  input.on("close", () => {
    if (active) active.agent?.abort();
    failPending(new Error("pi bridge stdin closed"));
  });
};

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  startProcess();
}
