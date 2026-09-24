(() => {
  "use strict";

  const state = { goals: [], goal: null, config: null, toastTimer: null };
  const $ = (id) => document.getElementById(id);

  function el(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    if (options.className) node.className = options.className;
    if (options.text !== undefined) node.textContent = options.text;
    if (options.type) node.type = options.type;
    if (options.value !== undefined) node.value = options.value;
    if (options.name) node.name = options.name;
    if (options.title) node.title = options.title;
    if (options.disabled) node.disabled = true;
    for (const [key, value] of Object.entries(options.attrs || {})) node.setAttribute(key, value);
    for (const child of children) node.append(child);
    return node;
  }

  function showToast(message, isError = false) {
    const toast = $("toast");
    toast.textContent = message;
    toast.classList.toggle("error", isError);
    toast.classList.add("show");
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toast.classList.remove("show"), 4200);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function formData(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  function replaceOptions(select, items, placeholder) {
    while (select.firstChild) select.removeChild(select.firstChild);
    if (placeholder) select.append(el("option", { text: placeholder, value: "", attrs: { disabled: "", selected: "" } }));
    for (const item of items) select.append(el("option", { text: item.label, value: item.value }));
  }
  function renderParentChoices() {
    const select = $("parent-goal");
    const selected = select.value;
    while (select.firstChild) select.removeChild(select.firstChild);
    select.append(el("option", { text: "Top-level goal", value: "" }));
    for (const goal of state.goals) {
      select.append(el("option", { text: `${goal.kind} · ${goal.outcome}`, value: goal.id }));
    }
    if (state.goals.some((goal) => goal.id === selected)) select.value = selected;
  }

  async function refreshConfig() {
    try {
      state.config = await api("/api/config");
      const status = $("provider-status");
      if (state.config.provider_ready) {
        status.textContent = `Provider ready · ${state.config.model}`;
        status.classList.add("ready");
        status.classList.remove("error");
      } else {
        status.textContent = "Manual mode · provider unavailable";
        status.classList.add("error");
        status.classList.remove("ready");
      }
      $("worker-note").textContent = state.config.provider_ready
        ? `The configured pi-powered worker can produce an untrusted candidate for ${state.config.model}.`
        : "The model, API key, or pi bridge is not configured. Manual drafts remain fully usable; worker runs fail honestly rather than using a fake fallback.";
    } catch (error) {
      showToast(error.message, true);
    }
  }

  async function refreshGoals(query = "") {
    try {
      const payload = await api(query ? `/api/search?q=${encodeURIComponent(query)}` : "/api/goals");
      state.goals = payload.goals || [];
      renderGoalTree();
      if (state.goal) {
        const stillThere = state.goals.some((goal) => goal.id === state.goal.id);
        if (stillThere) await selectGoal(state.goal.id, false);
      }
    } catch (error) {
      showToast(error.message, true);
    }
  }

  function renderGoalTree() {
    const tree = $("goal-tree");
    while (tree.firstChild) tree.removeChild(tree.firstChild);
    renderParentChoices();
    if (!state.goals.length) {
      tree.append(el("p", { className: "muted tiny", text: "No goals yet. Start with a concrete outcome." }));
      return;
    }
    const roots = state.goals.filter((goal) => !goal.parent_id);
    const children = (parentId) => state.goals.filter((goal) => goal.parent_id === parentId);
    const rendered = new Set();
    const add = (goal, depth = 0) => {
      if (rendered.has(goal.id)) return;
      rendered.add(goal.id);
      const button = el("button", { className: `goal-node${state.goal && state.goal.id === goal.id ? " active" : ""}`, title: goal.outcome, attrs: { "data-goal": goal.id, "aria-label": `Open ${goal.outcome}` } }, [
        el("strong", { text: goal.outcome }),
        el("small", { text: `${goal.kind} · ${goal.status} · rev ${goal.revision}` }),
      ]);
      button.style.marginLeft = `${Math.min(depth, 3) * 10}px`;
      tree.append(button);
      for (const child of children(goal.id)) add(child, depth + 1);
    };
    for (const goal of roots) add(goal);
    for (const goal of state.goals) if (!rendered.has(goal.id)) add(goal);
  }

  async function selectGoal(goalId, notify = true) {
    try {
      state.goal = await api(`/api/goals/${encodeURIComponent(goalId)}`);
      const emptyState = $("empty-state");
      const goalView = $("goal-view");
      emptyState.classList.add("hidden");
      emptyState.hidden = true;
      goalView.classList.remove("hidden");
      goalView.hidden = false;
      goalView.setAttribute("aria-hidden", "false");
      renderGoal();
      renderGoalTree();
      if (notify) window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      showToast(error.message, true);
    }
  }

  function artifactSnapshot(artifact) {
    return artifact.applicability || artifact;
  }

  function isCurrentSnapshot(item, goal) {
    const snapshot = artifactSnapshot(item);
    return snapshot.revision === goal.revision
      && snapshot.input_version === goal.input_version
      && snapshot.authority_version === goal.authority_version;
  }
  function hasImportedHistory(goal) {
    return (goal.effects || []).some((effect) => effect.state === "imported" || effect.gateway_state === "imported")
      || (goal.events || []).some((event) => String(event.type || "").toLowerCase().includes("import"));
  }

  function currentEvidence(goal, currentArtifactIds) {
    return (goal.invocations || []).flatMap((invocation) => (
      (invocation.evidence || []).map((evidence) => ({ ...evidence, invocation }))
    )).filter((evidence) => (
      isCurrentSnapshot(evidence.invocation, goal)
      && currentArtifactIds.has(evidence.artifact_id)
    ));
  }

  function renderDimensions(goal, substantiveArtifacts) {
    const importedHistory = hasImportedHistory(goal);
    const currentArtifacts = substantiveArtifacts.filter((artifact) => isCurrentSnapshot(artifact, goal));
    const currentArtifactIds = new Set(currentArtifacts.map((artifact) => artifact.id));
    const recordedEvidence = currentEvidence(goal, currentArtifactIds);
    const evidence = importedHistory ? [] : recordedEvidence;
    const passingEvidence = evidence.filter((item) => item.passed && item.trusted);
    const activeInvocations = (goal.invocations || []).filter((invocation) => (
      invocation.status === "running" && isCurrentSnapshot(invocation, goal)
    ));
    const currentInvocationIds = new Set(
      (goal.invocations || [])
        .filter((invocation) => isCurrentSnapshot(invocation, goal))
        .map((invocation) => invocation.id),
    );
    const effects = (goal.effects || []).filter((effect) => currentInvocationIds.has(effect.invocation_id));
    const accepted = importedHistory
      ? 0
      : (goal.acceptances || []).filter((acceptance) => acceptance.revision === goal.revision).length;
    const committed = importedHistory ? 0 : effects.filter((effect) => effect.state === "committed").length;
    const approved = importedHistory ? 0 : effects.filter((effect) => effect.state === "approved").length;
    const prepared = importedHistory ? 0 : effects.filter((effect) => effect.state === "prepared").length;

    let workState = "No current candidate";
    if (importedHistory) workState = "Imported history";
    else if (goal.status === "paused") workState = "Paused";
    else if (goal.status === "cancelled") workState = "Cancelled";
    else if (currentArtifacts.length) workState = "Candidate available";
    else if (activeInvocations.length) workState = "In progress";
    $("dimension-work-state").textContent = workState;
    $("dimension-work-detail").textContent = importedHistory
      ? `${currentArtifacts.length} preserved product(s) · applicability not locally confirmed`
      : `${currentArtifacts.length} current product(s) · revision ${goal.revision} · input ${goal.input_version}`;

    let evidenceState = "Awaiting candidate";
    if (importedHistory) evidenceState = "Imported / not independently authenticated";
    else if (currentArtifacts.length && passingEvidence.length) evidenceState = "Human check passed";
    else if (currentArtifacts.length && evidence.length) evidenceState = "Assessment recorded";
    $("dimension-evidence-state").textContent = evidenceState;
    $("dimension-evidence-detail").textContent = importedHistory
      ? `${recordedEvidence.length} recorded · local reassessment required`
      : `${passingEvidence.length} trusted passing · ${evidence.length} current check(s)`;

    let deliveryState = "Not proposed";
    if (importedHistory) deliveryState = "Imported / unverified historical claim";
    else if (committed) deliveryState = "Committed";
    else if (approved) deliveryState = "Approved";
    else if (prepared) deliveryState = "Prepared";
    else if (accepted) deliveryState = "Accepted";
    $("dimension-delivery-state").textContent = deliveryState;
    $("dimension-delivery-detail").textContent = importedHistory
      ? `${(goal.acceptances || []).length} historical acceptance(s) · permissions revoked`
      : `${accepted} accepted · ${prepared} prepared · ${approved} approved · ${committed} committed`;

    return { currentArtifacts, evidence, importedHistory };
  }

  function applicabilityText(artifact, goal) {
    const snapshot = artifactSnapshot(artifact);
    const label = hasImportedHistory(goal)
      ? "Imported history"
      : (isCurrentSnapshot(artifact, goal) ? "Current" : "Historical");
    return `${label} · source revision ${snapshot.revision} · input ${snapshot.input_version}`;
  }


  function renderGoal() {
    const goal = state.goal;
    if (!goal) return;
    $("goal-kind").textContent = `${goal.kind} · revision ${goal.revision}`;
    $("goal-outcome").textContent = goal.outcome;
    $("goal-meta").textContent = `Input v${goal.input_version} · authority v${goal.authority_version} · ${goal.criteria || "No criteria written yet."}`;
    $("goal-status").textContent = goal.status;
    $("goal-status").className = `status-pill ${["active", "draft"].includes(goal.status) ? "ready" : "error"}`;
    const importedNotice = $("import-notice");
    const importedHistory = hasImportedHistory(goal);
    importedNotice.classList.toggle("hidden", !importedHistory);
    importedNotice.hidden = !importedHistory;
    importedNotice.setAttribute("aria-hidden", importedHistory ? "false" : "true");

    const requests = $("request-list");
    while (requests.firstChild) requests.removeChild(requests.firstChild);
    for (const [index, request] of goal.requests.entries()) {
      const item = el("div", { className: `request-item${index === goal.requests.length - 1 ? " latest" : ""}` }, [
        el("p", { text: request.text }),
        el("small", { text: `${request.control || "request"} · input v${request.input_version} · ${request.created_at}` }),
      ]);
      requests.append(item);
    }
    $("revision-form").elements.outcome.value = goal.outcome;
    $("revision-form").elements.criteria.value = goal.criteria;
    $("revision-form").elements.constraints.value = goal.constraints;

    const artifacts = goal.artifacts || [];
    const substantiveArtifacts = artifacts.filter((artifact) => artifact.kind !== "context");
    const { currentArtifacts, evidence } = renderDimensions(goal, substantiveArtifacts);
    const currentArtifactIds = importedHistory
      ? new Set()
      : new Set(currentArtifacts.map((artifact) => artifact.id));
    $("artifact-count").textContent = `${substantiveArtifacts.length} substantive · ${artifacts.length - substantiveArtifacts.length} context in trace/export`;
    const artifactList = $("artifact-list");
    while (artifactList.firstChild) artifactList.removeChild(artifactList.firstChild);
    if (!substantiveArtifacts.length) artifactList.append(el("p", { className: "muted tiny", text: "No substantive product yet. Save a manual draft or run the configured worker." }));
    for (const artifact of substantiveArtifacts) {
      const current = currentArtifactIds.has(artifact.id);
      const card = el("article", { className: "artifact-card" });
      const head = el("div", { className: "artifact-head" }, [
        el("h4", { text: artifact.name || artifact.kind }),
        el("span", { className: "badge", text: `${artifact.kind} · ${artifact.trust} · ${current ? "current" : "historical"}` }),
      ]);
      card.append(head);
      card.append(el("div", { className: "artifact-content", text: artifact.content }));
      card.append(el("div", { className: "artifact-foot" }, [
        el("span", { text: `sha ${(artifact.content_hash || "").slice(0, 12)}` }),
        el("span", { text: applicabilityText(artifact, goal) }),
        el("span", { text: `invocation ${artifact.invocation_id.slice(-12)}` }),
        el("span", { className: "provenance", text: artifact.limitations || "No limitations recorded." }),
      ]));
      artifactList.append(card);
    }

    const assessmentItems = substantiveArtifacts.map((artifact) => ({
      value: artifact.id,
      label: `${artifact.name || artifact.kind} · ${applicabilityText(artifact, goal)}`,
    }));
    const currentItems = substantiveArtifacts.filter((artifact) => currentArtifactIds.has(artifact.id)).map((artifact) => ({
      value: artifact.id,
      label: `${artifact.name || artifact.kind} · current`,
    }));
    replaceOptions($("assessment-artifact"), assessmentItems, "Choose a candidate");
    for (const id of ["effect-artifact", "accept-artifact"]) replaceOptions($(id), currentItems, "Choose a current candidate");
    const evidenceItems = evidence.map((item) => ({ value: item.id, label: `${item.check} · ${item.passed ? "passed" : "failed"}${item.trusted ? " · human" : ""}` }));
    for (const id of ["effect-evidence", "accept-evidence"]) replaceOptions($(id), evidenceItems, "Choose current evidence");

    const effects = $("effect-list");
    while (effects.firstChild) effects.removeChild(effects.firstChild);
    if (!goal.effects.length) effects.append(el("p", { className: "muted tiny", text: "No effect proposal yet." }));
    for (const effect of goal.effects) {
      const effectLabel = effect.state === "imported" || effect.gateway_state === "imported"
        ? "Imported / unverified historical claim"
        : effect.state;
      const card = el("div", { className: "effect-card" }, [
        el("strong", { text: `${effectLabel} · ${effect.target}` }),
        el("p", { text: `Expected v${effect.expected_version} · operation ${effect.operation_id}` }),
      ]);
      const actions = el("div", { className: "button-row wrap" });
      if (effect.state === "prepared") actions.append(el("button", { className: "button", text: "Approve", attrs: { "data-effect": effect.id, "data-action": "approve" } }));
      if (effect.state === "approved") actions.append(el("button", { className: "button warn", text: "Commit mock effect", attrs: { "data-effect": effect.id, "data-action": "commit" } }));
      if (effect.state === "unresolved") actions.append(el("button", { className: "button", text: "Reconcile", attrs: { "data-effect": effect.id, "data-action": "reconcile" } }));
      card.append(actions);
      effects.append(card);
    }

    const events = $("event-list");
    while (events.firstChild) events.removeChild(events.firstChild);
    for (const event of [...goal.events].reverse()) {
      events.append(el("div", { className: "event-row" }, [
        el("time", { text: event.created_at }),
        el("span", { text: `${event.type}  ${JSON.stringify(event.payload)}` }),
      ]));
    }
  }

  async function refreshSelected(message) {
    if (state.goal) await selectGoal(state.goal.id, false);
    await refreshGoals($("search-input").value.trim());
    if (message) showToast(message);
  }

  async function handleError(task) {
    try { await task(); } catch (error) { showToast(error.message, true); }
  }

  $("goal-form").addEventListener("submit", (event) => handleError(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    data.parent_id = data.parent_id || null;
    const goal = await api("/api/goals", { method: "POST", body: JSON.stringify(data) });
    form.reset();
    await refreshGoals();
    await selectGoal(goal.id);
    showToast("Goal created");
  }));
  $("revision-form").addEventListener("submit", (event) => handleError(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    await api(`/api/goals/${state.goal.id}/revise`, {
      method: "POST",
      body: JSON.stringify({ ...data, expected_revision: state.goal.revision }),
    });
    await refreshSelected("Revision saved; older work remains traceable but stale");
  }));

  $("request-form").addEventListener("submit", (event) => handleError(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    await api(`/api/goals/${state.goal.id}/request`, { method: "POST", body: JSON.stringify({ text: data.text, control: data.control || null }) });
    form.reset();
    await refreshSelected("Request recorded");
  }));

  for (const button of $("request-form").querySelectorAll("button[data-control]")) {
    button.addEventListener("click", () => handleError(async () => {
      const form = $("request-form");
      const data = formData(form);
      if (!data.text) { showToast("Write the request before applying a control.", true); return; }
      await api(`/api/goals/${state.goal.id}/request`, { method: "POST", body: JSON.stringify({ text: data.text, control: button.dataset.control }) });
      form.reset();
      await refreshSelected("Request and control recorded");
    }));
  }

  $("draft-form").addEventListener("submit", (event) => handleError(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    await api(`/api/goals/${state.goal.id}/artifacts`, { method: "POST", body: JSON.stringify(data) });
    form.reset();
    form.elements.name.value = "Manual draft";
    await refreshSelected("Human draft saved with provenance");
  }));

  $("run-button").addEventListener("click", () => handleError(async () => {
    const result = await api(`/api/goals/${state.goal.id}/run`, { method: "POST", body: JSON.stringify({ context: "" }) });
    showToast(`Run ${result.run_id.slice(-10)} started`);
    setTimeout(() => refreshSelected(), 1200);
  }));
  $("cancel-button").addEventListener("click", () => handleError(async () => {
    await api(`/api/goals/${state.goal.id}/cancel`, { method: "POST", body: "{}" });
    showToast("Cancellation requested");
  }));

  $("assessment-form").addEventListener("submit", (event) => handleError(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    const artifact = state.goal.artifacts.find((item) => item.id === data.artifact_id);
    await api(`/api/goals/${state.goal.id}/assess`, { method: "POST", body: JSON.stringify({ ...data, passed: form.elements.passed.checked, invocation_id: artifact.invocation_id }) });
    form.reset();
    await refreshSelected("Trusted assessment recorded");
  }));

  $("effect-form").addEventListener("submit", (event) => handleError(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    const artifact = state.goal.artifacts.find((item) => item.id === data.artifact_id);
    await api(`/api/goals/${state.goal.id}/prepare-effect`, { method: "POST", body: JSON.stringify({ ...data, expected_version: Number(data.expected_version), invocation_id: artifact.invocation_id }) });
    await refreshSelected("Exact effect proposal prepared");
  }));

  $("accept-form").addEventListener("submit", (event) => handleError(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    await api(`/api/goals/${state.goal.id}/accept`, { method: "POST", body: JSON.stringify({ ...data, expected_revision: state.goal.revision }) });
    await refreshSelected("Acceptance recorded");
  }));

  $("effect-list").addEventListener("click", (event) => handleError(async () => {
    const button = event.target.closest("button[data-effect]");
    if (!button) return;
    const action = button.dataset.action;
    await api(`/api/effects/${button.dataset.effect}/${action}`, { method: "POST", body: JSON.stringify({}) });
    await refreshSelected(`Effect ${action} complete`);
  }));

  $("goal-tree").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-goal]");
    if (button) selectGoal(button.dataset.goal);
  });
  $("search-input").addEventListener("input", () => refreshGoals($("search-input").value.trim()));
  $("refresh-button").addEventListener("click", () => handleError(async () => { await refreshConfig(); await refreshGoals($("search-input").value.trim()); showToast("Workspace refreshed"); }));

  $("export-button").addEventListener("click", () => handleError(async () => {
    const payload = await api("/api/export");
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = el("a", { attrs: { href: url, download: "goal-native-workspace.json" } });
    anchor.click();
    URL.revokeObjectURL(url);
    showToast("Workspace export downloaded");
  }));
  $("import-input").addEventListener("change", () => handleError(async () => {
    const file = $("import-input").files[0];
    if (!file) return;
    const payload = JSON.parse(await file.text());
    await api("/api/import", { method: "POST", body: JSON.stringify({ data: payload }) });
    state.goal = null;
    const goalView = $("goal-view");
    const emptyState = $("empty-state");
    goalView.classList.add("hidden");
    goalView.hidden = true;
    goalView.setAttribute("aria-hidden", "true");
    emptyState.classList.remove("hidden");
    emptyState.hidden = false;
    await refreshGoals();
    showToast("Workspace reopened from export");
  }));

  Promise.all([refreshConfig(), refreshGoals()]);
})();
