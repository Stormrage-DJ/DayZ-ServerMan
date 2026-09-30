"""Load and compose bundled frontend assets without a network server."""

from __future__ import annotations

from pathlib import Path


# Marker in the shell document replaced with the concatenated stylesheets
STYLE_MARKER = "<!-- INLINE_STYLES -->"
# Marker in the shell document replaced with the concatenated scripts
SCRIPT_MARKER = "<!-- INLINE_SCRIPT -->"


def source_frontend_root(module_file: Path = Path(__file__)) -> Path:
    """Locate the frontend directory from the source tree layout."""
    # Walk upward until the source layout root that owns the frontend is found
    for parent in module_file.resolve(strict=False).parents:
        if parent.name.casefold() == "src":
            nested = parent / "frontend"
            return nested if nested.is_dir() else parent.parent / "frontend"
    raise ValueError("source layout must contain a src directory")


def resolve_frontend_root(manager_root: Path, module_file: Path = Path(__file__)) -> Path:
    """Return the packaged frontend directory or fall back to the source tree."""
    # Prefer the frontend bundled beside the portable manager root
    packaged = manager_root.resolve(strict=False) / "frontend"
    if packaged.is_dir():
        return packaged
    return source_frontend_root(module_file)


def compose_shell_html(frontend_root: Path) -> str:
    """Inline every frontend asset into the shell document for offline use."""
    root = frontend_root.resolve(strict=True)
    document = (root / "index.html").read_text(encoding="utf-8")
    # Require exactly one marker for styles and one for scripts
    if document.count(STYLE_MARKER) != 1 or document.count(SCRIPT_MARKER) != 1:
        raise ValueError("frontend document must contain one style and script marker")
    # Read every stylesheet that the composed shell inlines
    tokens = (root / "tokens.css").read_text(encoding="utf-8")
    controls = (root / "controls.css").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")
    mods_styles = (root / "mods.css").read_text(encoding="utf-8")
    overview_styles = (root / "overview.css").read_text(encoding="utf-8")
    profiles_styles = (root / "profiles.css").read_text(encoding="utf-8")
    # Read every script in the order the shell expects
    shell_ui_script = (root / "shell_ui.js").read_text(encoding="utf-8")
    workspace_script = (root / "workspace_context.js").read_text(encoding="utf-8")
    transition_script = (root / "transition_guard.js").read_text(encoding="utf-8")
    profile_context_script = (root / "profile_context.js").read_text(encoding="utf-8")
    configuration_catalog_script = (root / "configuration_catalog.js").read_text(encoding="utf-8")
    tweaks_catalog_script = (root / "tweaks_catalog.js").read_text(encoding="utf-8")
    tweaks_render_script = (root / "tweaks_render.js").read_text(encoding="utf-8")
    tweaks_script = (root / "tweaks.js").read_text(encoding="utf-8")
    configuration_edit_script = (root / "configuration_edit.js").read_text(encoding="utf-8")
    configuration_context_script = (root / "configuration_context.js").read_text(encoding="utf-8")
    configuration_script = (root / "configuration.js").read_text(encoding="utf-8")
    backup_display_script = (root / "backup_display.js").read_text(encoding="utf-8")
    backup_script = (root / "backups.js").read_text(encoding="utf-8")
    restore_script = (root / "restore.js").read_text(encoding="utf-8")
    profiles_script = (root / "profiles.js").read_text(encoding="utf-8")
    profile_create_script = (root / "profile_create.js").read_text(encoding="utf-8")
    profiles_mods_script = (root / "profiles_mods.js").read_text(encoding="utf-8")
    profile_copy_mods_script = (root / "profile_copy_mods.js").read_text(encoding="utf-8")
    profile_delete_script = (root / "profile_delete.js").read_text(encoding="utf-8")
    migration_script = (root / "migration.js").read_text(encoding="utf-8")
    settings_script = (root / "settings.js").read_text(encoding="utf-8")
    mods_script = (root / "mods.js").read_text(encoding="utf-8")
    mods_display_script = (root / "mods_display.js").read_text(encoding="utf-8")
    mod_publication_script = (root / "mod-publication.js").read_text(encoding="utf-8")
    overview_backup_script = (root / "overview_backup.js").read_text(encoding="utf-8")
    overview_schedule_script = (root / "overview_schedule.js").read_text(encoding="utf-8")
    overview_readiness_script = (root / "overview_readiness.js").read_text(encoding="utf-8")
    overview_dialog_script = (root / "overview_lifecycle_dialog.js").read_text(encoding="utf-8")
    overview_script = (root / "overview.js").read_text(encoding="utf-8")
    overview_status_script = (root / "overview_status.js").read_text(encoding="utf-8")
    logs_script = (root / "logs.js").read_text(encoding="utf-8")
    app_script = (root / "app.js").read_text(encoding="utf-8")
    # Substitute the style marker with the concatenated stylesheets
    document = document.replace(
        STYLE_MARKER,
        f"<style>\n{tokens}\n{controls}\n{styles}\n{mods_styles}\n{overview_styles}\n{profiles_styles}\n</style>",
    )
    script = f"{shell_ui_script}\n{workspace_script}\n{transition_script}\n{profile_context_script}\n{configuration_catalog_script}\n{tweaks_catalog_script}\n{tweaks_render_script}\n{tweaks_script}\n{configuration_edit_script}\n{configuration_context_script}\n{configuration_script}\n{backup_display_script}\n{backup_script}\n{restore_script}\n{profiles_mods_script}\n{profile_copy_mods_script}\n{profile_create_script}\n{profiles_script}\n{profile_delete_script}\n{migration_script}\n{settings_script}\n{mods_script}\n{mods_display_script}\n{mod_publication_script}\n{overview_backup_script}\n{overview_schedule_script}\n{overview_readiness_script}\n{overview_dialog_script}\n{overview_script}\n{overview_status_script}\n{logs_script}\n{app_script}"
    # Substitute the script marker and return the composed document
    return document.replace(SCRIPT_MARKER, f"<script>\n{script}\n</script>")
