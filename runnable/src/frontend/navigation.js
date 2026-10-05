// Sidebar navigation: groups, items, and icons built from the section registry, and the update badge on Mods.
"use strict";

// Namespace of the inline icons; written in two parts because the shell holds no address of a remote resource.
const NAVIGATION_SVG = "http:" + "//www.w3.org/2000/svg";
// Section whose item carries the update badge, and the count that was spoken last per profile.
const navigationBadgeState = { section: "mods", profileId: "", count: 0 };

// Build one inline icon from the path data of a section.
function navigationIcon(pathData) {
  const icon = document.createElementNS(NAVIGATION_SVG, "svg");
  icon.setAttribute("class", "nav-icon");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  icon.setAttribute("focusable", "false");
  const path = document.createElementNS(NAVIGATION_SVG, "path");
  path.setAttribute("d", pathData);
  icon.append(path);
  return icon;
}

// Build the item of one section: icon, label, and the two badge parts (drawn mark and spoken words).
function navigationItem(section) {
  const node = window.ServerManUi.element;
  const item = node("button", "nav-item"); item.type = "button"; item.dataset.section = section.id;
  const badge = node("span", "nav-badge"); badge.setAttribute("aria-hidden", "true"); badge.hidden = true;
  item.append(navigationIcon(section.icon), node("span", "nav-label", section.title), badge,
    node("span", "sr-only nav-badge-name"));
  // Route the press through the guarded section switch of the shell.
  item.addEventListener("click", () => setSection(section.id));
  return item;
}

// Build the three labelled groups from the registry; registration order is the order inside a group.
function buildNavigation() {
  const list = document.querySelector(".nav-list");
  if (!list) return;
  const node = window.ServerManUi.element;
  const sections = window.ServerManSections.ids().map((id) => window.ServerManSections.get(id));
  const groups = window.ServerManSections.groups.map((group) => {
    const items = sections.filter((section) => section.group === group.id);
    if (!items.length) return null;
    // The group label is a plain text, so the page title stays the first heading.
    const wrapper = node("div", "nav-group"); wrapper.setAttribute("role", "group");
    const label = node("span", "nav-group-label", group.label); label.id = `nav-group-${group.id}`;
    wrapper.setAttribute("aria-labelledby", label.id);
    wrapper.append(label, ...items.map(navigationItem));
    return wrapper;
  }).filter(Boolean);
  list.replaceChildren(...groups);
}

// Mark the item of the visible section as the current page.
function setCurrentNavigation(section) {
  document.querySelectorAll(".nav-item").forEach((button) => {
    if (button.dataset.section === section) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

// Write one badge model into a badge element; the form is a class, the mark is the text.
function drawUpdateBadge(node, badge) {
  const wide = badge.form === "count" && badge.text.length > 1 ? "is-wide" : "";
  const className = `nav-badge is-${badge.form} ${wide}`.trim();
  if (node.className !== className) node.className = className;
  if (node.textContent !== badge.text) node.textContent = badge.text;
  node.hidden = badge.form === "none";
}

// Speak a risen count once; a fall, an equal count, and another profile's first count of zero stay silent.
function announceBadgeRise(badge) {
  const profileId = window.ServerManProfileContext.selectedId() || "";
  // The count of another profile is no rise: start again from zero for the new selection.
  if (profileId !== navigationBadgeState.profileId) {
    navigationBadgeState.profileId = profileId; navigationBadgeState.count = 0;
  }
  const risen = badge.count > navigationBadgeState.count;
  navigationBadgeState.count = badge.count;
  if (!risen) return;
  const profile = window.ServerManProfileContext.profiles().find((item) => item.profile_id === profileId);
  // The same parts as the spoken badge name, so a mod that is only not downloaded is not called an update (QF-061).
  speakOperation(`Mods need attention for ${profile ? profile.display_name : "this server"}: ${badge.name}.`);
}

// Redraw the badge of the Mods item and its accessible name from the current update state.
function renderNavigationBadge() {
  const item = document.querySelector(`.nav-item[data-section="${navigationBadgeState.section}"]`);
  if (!item) return;
  const badge = window.ServerManUpdateStatus.badge();
  drawUpdateBadge(item.querySelector(".nav-badge"), badge);
  // The words follow the label, so the item reads "Mods, 2 updates available".
  const name = item.querySelector(".nav-badge-name");
  const text = badge.name ? `, ${badge.name}` : "";
  if (name.textContent !== text) name.textContent = text;
  announceBadgeRise(badge);
}

// Wire the one "Create profile" action of the sidebar to the guarded section switch.
function wireSidebarCreate() {
  document.getElementById("sidebar-create-profile")?.addEventListener("click", () => setSection("profiles"));
}

// Build the navigation once the registry is complete, and follow the update state.
buildNavigation();
wireSidebarCreate();
document.addEventListener("serverman:update-status", renderNavigationBadge);
document.addEventListener("serverman:profile-change", renderNavigationBadge);

// Publish the navigation for the shell loop and the narrow-window badge.
window.ServerManNavigation = Object.freeze({
  build: buildNavigation,
  setCurrent: setCurrentNavigation,
  drawBadge: drawUpdateBadge,
});
