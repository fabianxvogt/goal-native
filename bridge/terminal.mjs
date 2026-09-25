// Presentation only. Store, credentials, tools and execution remain in Python.
import fs from "node:fs";
import { createInterface } from "node:readline";
import {
  Container, Editor, Markdown, ProcessTerminal, Spacer, Text, TuiMainScreen,
  matchesKey, truncateToWidth,
} from "../upstream/pi/packages/tui/dist/index.js";

const [eventFd, commandFd, controllerPid] = process.argv.slice(2).map(Number);
const events = fs.createReadStream(null, { fd: eventFd, encoding: "utf8" });
const commands = fs.createWriteStream(null, { fd: commandFd });
const send = (value) => commands.write(JSON.stringify(value) + "\n");
const safe = (value) => String(value ?? "").replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, "");
const colors = { cyan: 36, dim: 2, yellow: 33, red: 31, green: 32, bold: 1 };
const color = (name, text) => "NO_COLOR" in process.env ? text : `\x1b[${colors[name]}m${text}\x1b[0m`;
const dim = (text) => color("dim", text);
const cyan = (text) => color("cyan", text);
const identity = (text) => text;
const theme = {
  heading: cyan, link: cyan, linkUrl: dim, code: cyan, codeBlock: identity,
  codeBlockBorder: dim, quote: dim, quoteBorder: dim, hr: dim, listBullet: cyan,
  bold: (text) => color("bold", text), italic: identity,
  strikethrough: identity, underline: identity,
};
const terminal = new ProcessTerminal();
const tui = new TuiMainScreen(terminal);
const header = new Text("", 1, 0);
const transcript = new Container();
const activity = new Text("", 1, 0);
let model = "", runtime = "", budget = 0, selected = null, goals = [], busy = true;
let controllerReady = false;
let output = null, outputText = "", assistant = null, assistantText = "";
let stopped = false;
let cancelling = false;

const goalPanel = {
  invalidate() {},
  render(width) {
    const available = Math.max(1, Math.min(4, Math.floor(((process.stdout.rows || 24) - 12) / 2)));
    const current = goals.find((goal) => goal.id === selected);
    const shown = current ? [current, ...goals.filter((goal) => goal.id !== selected)].slice(0, available) : goals.slice(0, available);
    const lines = [dim(truncateToWidth(` Goals (${goals.length})  /goals · /resume N`, width))];
    if (!shown.length) lines.push(dim(truncateToWidth(" ○ New session — type a request to begin", width)));
    for (const goal of shown) {
      const marker = goal.id === selected ? "›" : " ";
      const light = color(goal.color, "●");
      lines.push(truncateToWidth(` ${marker} ${light} ${goal.number}. ${safe(goal.title).replace(/\s+/g, " ")}`, width));
      lines.push(dim(truncateToWidth(`     ${safe(goal.label)} · goal: ${safe(goal.status)} · ${goal.id.slice(0, 8)}`, width)));
    }
    if (goals.length > shown.length) lines.push(dim(truncateToWidth(` +${goals.length - shown.length} more /goals`, width)));
    return lines;
  },
};
const editor = new Editor(tui, {
  borderColor: cyan,
  selectList: { selectedPrefix: cyan, selectedText: cyan, description: dim, scrollInfo: dim, noMatch: dim },
}, { paddingX: 1 });
editor.disableSubmit = true;
const commandNames = ["help", "goals", "sessions", "resume", "new", "status", "budget", "continue", "files", "changes", "diff", "export", "exit"];
editor.setAutocompleteProvider({
  triggerCharacters: ["/"],
  async getSuggestions(lines, row, col) {
    const prefix = lines[row].slice(0, col);
    if (!/^\/\w*$/.test(prefix)) return null;
    const items = commandNames.map((name) => ({ value: `/${name}`, label: `/${name}` })).filter((item) => item.value.startsWith(prefix));
    return items.length ? { items, prefix } : null;
  },
  applyCompletion(lines, row, col, item, prefix) {
    const updated = [...lines];
    updated[row] = lines[row].slice(0, col - prefix.length) + item.value + " " + lines[row].slice(col);
    return { lines: updated, cursorLine: row, cursorCol: col - prefix.length + item.value.length + 1 };
  },
});
const footer = new Text("", 1, 0);
function updateFooter() {
  footer.setText(dim(`${model} · ${runtime} · ${Number(budget).toLocaleString("en-US")} context\n${busy ? "Working · Ctrl-C stops" : "Enter send · Alt-Enter newline · Tab commands · Ctrl-D exit"} · draft only`));
}
function newOutput() {
  assistant = null;
  assistantText = "";
  if (!output) {
    outputText = "";
    output = new Text("", 1, 0);
    transcript.addChild(output);
  }
}
function boundary() {
  output = null;
  outputText = "";
  assistant = null;
  assistantText = "";
}
function submit(text) {
  if (busy || !text.trim()) return;
  busy = true;
  editor.disableSubmit = true;
  editor.addToHistory(text);
  editor.setText("");
  boundary();
  transcript.addChild(new Text(cyan("You") + "\n" + safe(text), 1, 1));
  updateFooter();
  send({ type: "submit", text });
  tui.requestRender();
}
editor.onSubmit = submit;
function interrupt() {
  if (busy && !cancelling) {
    cancelling = true;
    activity.setText(color("yellow", "Stopping… waiting for cleanup"));
    process.kill(controllerPid, "SIGINT");
  } else if (!busy) {
    editor.setText("");
    activity.setText(dim("Input cleared · /exit to quit"));
  }
  tui.requestRender();
}
tui.addInputListener((data) => {
  if (matchesKey(data, "ctrl+c")) {
    interrupt();
    return { consume: true };
  }
  if (matchesKey(data, "ctrl+d") && !editor.getText() && !busy) {
    submit("/exit");
    return { consume: true };
  }
  // Do not accept a hidden queued request while the controller is executing.
  if (busy) return { consume: true };
});
for (const component of [header, new Spacer(1), transcript, activity, new Spacer(1), goalPanel, editor, footer]) tui.addChild(component);
tui.setFocus(editor);

function close(code = 0) {
  if (stopped) return;
  stopped = true;
  let interrupted = false;
  if (code && busy && controllerReady) {
    try {
      process.kill(controllerPid, "SIGINT");
      interrupted = true;
    } catch (error) {
      if (error.code !== "ESRCH") process.stderr.write(`Controller interrupt failed: ${safe(error.message)}\n`);
    }
  }
  tui.stop();
  events.destroy();
  // 130 acknowledges our own interrupt; Python must not signal a second time.
  commands.end(() => process.exit(interrupted ? 130 : code));
}
createInterface({ input: events }).on("line", (line) => {
  try {
    const event = JSON.parse(line);
    if (event.type === "close") return close();
    if (event.type === "header") {
      model = safe(event.model);
      runtime = safe(event.runtime);
      budget = event.budget;
      header.setText(cyan("Goal Native") + dim("  /  persistent work, reviewed delivery") +
        (event.source ? `\n${dim("Source selected (not sent): ")}${safe(event.source)}` : ""));
    } else if (event.type === "goals") {
      goals = event.goals;
      selected = event.selected;
      budget = event.budget;
    } else if (event.type === "output") {
      newOutput();
      outputText += safe(event.text);
      output.setText(outputText);
    } else if (event.type === "assistant") {
      output = null;
      if (!assistant) {
        assistantText = "";
        transcript.addChild(new Text(cyan("Assistant"), 1, 0));
        assistant = new Markdown("", 1, 1, theme);
        transcript.addChild(assistant);
      }
      assistantText += safe(event.text);
      assistant.setText(assistantText);
    } else if (event.type === "assistant_end") {
      boundary();
    } else if (event.type === "activity") {
      activity.setText(color(event.color || "dim", safe(event.text)));
    } else if (event.type === "prompt") {
      boundary();
      controllerReady = true;
      busy = false;
      cancelling = false;
      editor.disableSubmit = false;
      activity.setText("");
    }
    updateFooter();
    tui.requestRender();
  } catch (error) {
    process.stderr.write(`Terminal interface error: ${safe(error.message)}\n`);
    close(1);
  }
}).on("close", () => close(1));
commands.on("error", () => close(1));
process.on("SIGTERM", () => close(1));
process.on("SIGINT", interrupt);
process.on("SIGHUP", () => close(1));
tui.start();
send({ type: "ready" });
