// Page heading context: the server line under the title and the update badge button of a narrow window.
"use strict";

// Section that the heading describes, and a text that a page set in place of the server name.
const pageContextState = { section: "overview", override: "" };

// Return the display name of the selected profile, or an empty text when no profile exists.
function pageContextProfileName() {
  const selectedId = window.ServerManProfileContext.selectedId();
  const profile = window.ServerManProfileContext.profiles().find((item) => item.profile_id === selectedId);
  return profile ? profile.display_name : "";
}

// Draw the context line: "Server: <name>" with the state for a narrow window, or the text of the page.
function renderPageServer() {
  const line = document.getElementById("page-server");
  if (!line) return;
  const section = window.ServerManSections.get(pageContextState.section);
  line.hidden = !section?.usesProfile;
  if (line.hidden) return;
  const node = window.ServerManUi.element;
  const name = pageContextProfileName();
  // A page text (the creation form) or the missing profile replaces the name.
  if (pageContextState.override || !name) {
    const text = pageContextState.override || "No server profile yet";
    if (line.dataset.shown !== text) { line.replaceChildren(text); line.dataset.shown = text; }
    return;
  }
  const status = window.ServerManServerState.current();
  const [label, tone] = status ? window.ServerManOverviewReadiness.presentation(status)
    : ["Checking…", "status-neutral"];
  // Rebuild only when the name or the state changed, so nothing is announced or redrawn in vain.
  const shown = `${name}|${label}|${tone}`;
  if (line.dataset.shown === shown) return;
  line.dataset.shown = shown;
  line.replaceChildren("Server: ", node("strong", "", name), " ",
    node("span", `status-label page-server-state ${tone}`, label));
}

// Draw the badge button of the heading; it exists for a narrow window, where the sidebar is closed.
function renderPageBadge() {
  const button = document.getElementById("page-update-badge");
  if (!button) return;
  const badge = window.ServerManUpdateStatus.badge();
  // The Mods page shows the state itself, and an empty badge needs no button.
  button.hidden = badge.form === "none" || pageContextState.section === "mods";
  window.ServerManNavigation.drawBadge(button.querySelector(".nav-badge"), badge);
  // The first letter is upper case because the name stands alone here.
  const name = badge.name ? `${badge.name[0].toUpperCase()}${badge.name.slice(1)}. Open Mods.` : "Open Mods.";
  if (button.getAttribute("aria-label") !== name) { button.setAttribute("aria-label", name); button.title = name; }
}

// Show the heading context of a section; every section open drops a text that a page set before.
function showPageContext(section) {
  pageContextState.section = section;
  pageContextState.override = "";
  document.body.dataset.section = section;
  renderPageServer();
  renderPageBadge();
}

// Let a page put its own text in the context line until the next section open.
function setPageContextText(text) {
  pageContextState.override = text || "";
  renderPageServer();
}

// Follow the selection, the server state, and the update state.
document.addEventListener("serverman:profile-change", () => { pageContextState.override = ""; renderPageServer(); });
document.addEventListener("serverman:server-status", renderPageServer);
document.addEventListener("serverman:update-status", renderPageBadge);
document.addEventListener("serverman:profile-change", renderPageBadge);
// The heading badge opens Mods through the guarded section switch.
document.getElementById("page-update-badge")?.addEventListener("click", () => setSection("mods"));

// Publish the heading context for the shell loop and the pages.
window.ServerManPageContext = Object.freeze({
  show: showPageContext,
  setText: setPageContextText,
  refresh: renderPageServer,
});
