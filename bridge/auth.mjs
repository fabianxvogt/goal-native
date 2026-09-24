import { createInterface } from "node:readline";
import { homedir } from "node:os";
import { stderr, stdin } from "node:process";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { AuthStorage } from "../upstream/pi/packages/coding-agent/dist/core/auth-storage.js";
import { createModels } from "../upstream/pi/packages/ai/dist/index.js";
import { openaiCodexProvider } from "../upstream/pi/packages/ai/dist/providers/openai-codex.js";

const PROVIDER = "openai-codex";
const DEFAULT_AUTH_FILE = join(homedir(), ".config", "goal-native", "auth.json");
const LOGIN_METHODS = new Set(["browser", "device_code"]);

class AuthCommandError extends Error {
  constructor(message, type = "auth") {
    super(message);
    this.name = "AuthCommandError";
    this.type = type;
  }
}

function jsonLine(value) {
  return JSON.stringify(value, (_key, item) => (typeof item === "bigint" ? Number(item) : item));
}

function writeJson(value) {
  process.stdout.write(`${jsonLine(value)}\n`);
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  if (!["status", "login", "logout", "models"].includes(command)) {
    throw new AuthCommandError("command must be status, login, logout, or models", "usage");
  }

  let authFile;
  let method;
  for (let index = 0; index < rest.length; index += 1) {
    const option = rest[index];
    if (option === "--auth-file") {
      authFile = rest[++index];
      if (!authFile || authFile.startsWith("--")) {
        throw new AuthCommandError("--auth-file requires a path", "usage");
      }
    } else if (option === "--method") {
      method = rest[++index];
      if (!method || method.startsWith("--")) {
        throw new AuthCommandError("--method requires browser or device_code", "usage");
      }
    } else {
      throw new AuthCommandError(`unknown option: ${option}`, "usage");
    }
  }

  if (method !== undefined && !LOGIN_METHODS.has(method)) {
    throw new AuthCommandError("--method must be browser or device_code", "usage");
  }
  if (method !== undefined && command !== "login") {
    throw new AuthCommandError("--method is only valid for login", "usage");
  }
  return { command, authFile: resolveAuthFile(authFile), method };
}

export function resolveAuthFile(authFile) {
  if (authFile === undefined) return DEFAULT_AUTH_FILE;
  if (typeof authFile !== "string" || authFile.trim() === "") {
    throw new AuthCommandError("auth file path must be non-empty", "usage");
  }
  const expanded = authFile === "~" ? homedir() : authFile.startsWith("~/") ? join(homedir(), authFile.slice(2)) : authFile;
  return resolve(expanded);
}

function createCodexModels(authFile) {
  const credentials = AuthStorage.create(authFile);
  const models = createModels({ credentials });
  models.setProvider(openaiCodexProvider());
  return { credentials, models };
}

function validateOAuthCredential(credential) {
  if (credential === undefined) return undefined;
  if (
    credential?.type !== "oauth" ||
    typeof credential.access !== "string" ||
    typeof credential.refresh !== "string" ||
    !Number.isFinite(credential.expires)
  ) {
    throw new AuthCommandError("stored Codex credential is invalid", "auth");
  }
  return credential;
}

export async function readCodexStatus(authFile) {
  const { credentials } = createCodexModels(authFile);
  const signal = new AbortController().signal;
  const entry = (await credentials.list({ signal })).find((candidate) => candidate.providerId === PROVIDER);
  if (!entry) {
    return { provider: PROVIDER, configured: false, credential_type: null, expired: null };
  }
  if (entry.type !== "oauth") {
    throw new AuthCommandError("stored Codex credential is invalid", "auth");
  }
  const credential = validateOAuthCredential(await credentials.read(PROVIDER, { signal }));
  return {
    provider: PROVIDER,
    configured: credential !== undefined,
    credential_type: credential ? "oauth" : null,
    expired: credential ? credential.expires <= Date.now() : null,
  };
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

function redactText(value) {
  return String(value)
    .replace(/Bearer\s+[A-Za-z0-9._~+\/-]+/gi, "Bearer [redacted]")
    .replace(/(access|refresh)_token[=:]\s*[^\s,&}]+/gi, "$1_token=[redacted]")
    .replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "[redacted-token]");
}

function createInteraction(method) {
  if (!stdin.isTTY || !stderr.isTTY) {
    throw new AuthCommandError("login requires an interactive TTY", "usage");
  }

  const controller = new AbortController();
  let closing = false;
  const input = createInterface({ input: stdin, output: stderr, terminal: true });
  const cancel = () => controller.abort(new AuthCommandError("login cancelled", "cancelled"));
  const onProcessInterrupt = () => cancel();
  process.once("SIGINT", onProcessInterrupt);
  input.on("SIGINT", cancel);
  input.on("close", () => {
    if (!closing && !controller.signal.aborted) cancel();
  });

  const ask = (prompt, signal) =>
    new Promise((resolveAnswer, reject) => {
      const activeSignal = signal ? AbortSignal.any([controller.signal, signal]) : controller.signal;
      if (activeSignal.aborted) {
        reject(activeSignal.reason ?? new AuthCommandError("login cancelled", "cancelled"));
        return;
      }
      let settled = false;
      const cleanup = () => {
        activeSignal.removeEventListener("abort", onAbort);
      };
      const onAbort = () => {
        if (settled) return;
        settled = true;
        cleanup();
        closing = true;
        input.close();
        reject(activeSignal.reason ?? new AuthCommandError("login cancelled", "cancelled"));
      };
      activeSignal.addEventListener("abort", onAbort, { once: true });
      input.question(`${redactText(prompt.message)}${prompt.placeholder ? ` [${redactText(prompt.placeholder)}]` : ""} `, (answer) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolveAnswer(answer);
      });
    });

  const interaction = {
    signal: controller.signal,
    async prompt(prompt) {
      if (prompt.type === "select") {
        if (method !== undefined) return method;
        const choices = prompt.options.map((option, index) => `${index + 1}) ${option.label}`).join("  ");
        const answer = (await ask({ ...prompt, message: `${prompt.message}\n${choices}` }, prompt.signal)).trim();
        if (answer === "") return prompt.options[0]?.id ?? "";
        const numeric = Number.parseInt(answer, 10);
        if (Number.isInteger(numeric) && numeric >= 1 && numeric <= prompt.options.length) {
          return prompt.options[numeric - 1].id;
        }
        const selected = prompt.options.find((option) => option.id === answer);
        if (!selected) throw new AuthCommandError("invalid login method selection", "usage");
        return selected.id;
      }
      return ask(prompt, prompt.signal);
    },
    notify(event) {
      if (event.type === "auth_url") {
        stderr.write(`OpenAI Codex login URL: ${redactText(event.url)}\n`);
        stderr.write("Open this URL in your browser to finish login; no browser is opened automatically.\n");
      } else if (event.type === "device_code") {
        stderr.write(`OpenAI Codex device code: ${redactText(event.userCode)}\n`);
        stderr.write(`Open ${redactText(event.verificationUri)} to continue.\n`);
      } else if (event.type === "info" || event.type === "progress") {
        stderr.write(`${redactText(event.message)}\n`);
        for (const link of event.links ?? []) {
          stderr.write(`${redactText(link.label ?? "More information")}: ${redactText(link.url)}\n`);
        }
      }
    },
  };

  return {
    interaction,
    close() {
      closing = true;
      input.close();
      process.removeListener("SIGINT", onProcessInterrupt);
    },
  };
}

async function runCommand({ command, authFile, method }) {
  if (command === "status") return readCodexStatus(authFile);
  if (command === "models") {
    const models = createModels();
    models.setProvider(openaiCodexProvider());
    return { provider: PROVIDER, models: models.getModels(PROVIDER).map(modelSummary) };
  }

  const { models } = createCodexModels(authFile);
  if (command === "logout") {
    await models.logout(PROVIDER, { signal: new AbortController().signal });
    return { ok: true, provider: PROVIDER, logged_out: true };
  }

  const io = createInteraction(method);
  try {
    await models.login(PROVIDER, "oauth", io.interaction);
  } finally {
    io.close();
  }
  return { ok: true, ...await readCodexStatus(authFile) };
}

function errorResult(error) {
  if (error instanceof AuthCommandError) return { error: error.message, type: error.type };
  if (error?.name === "AbortError" || error?.type === "cancelled") {
    return { error: "login cancelled", type: "cancelled" };
  }
  if (error?.code === "oauth") return { error: "subscription authentication failed", type: "oauth" };
  if (error?.code === "auth") return { error: "authentication storage failed", type: "auth" };
  return { error: "authentication operation failed", type: "auth" };
}

export async function main(argv = process.argv.slice(2)) {
  try {
    writeJson(await runCommand(parseArgs(argv)));
    return 0;
  } catch (error) {
    writeJson(errorResult(error));
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().then((code) => {
    process.exitCode = code;
  });
}
