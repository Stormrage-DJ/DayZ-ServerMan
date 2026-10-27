"""Expose only scoped layout calls to the editor document."""

class MapEditorLayoutMethods:
    """Keep unrelated preferences outside the editor API."""

    def get_editor_layout(self):
        """Read layout without a mission or profile parameter."""
        return self._invoke_unbound("get_editor_layout", {})

    def save_editor_layout(self, patch):
        """Forward only the partial layout patch to the strict handler."""
        return self._invoke_unbound("save_editor_layout", {"patch": patch})
