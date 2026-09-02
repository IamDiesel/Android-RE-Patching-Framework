class PatchConflictException(Exception):
    """Wird geworfen, wenn ein Smali-Patch strukturell von der Zieldatei abweicht."""
    def __init__(self, patch_index: int, file_path: str, method_sig: str, expected_block: str, actual_block: str, edit_block: str):
        self.patch_index = patch_index
        self.file_path = file_path
        self.method_sig = method_sig
        self.expected_block = expected_block
        self.actual_block = actual_block
        self.edit_block = edit_block
        super().__init__(f"Konflikt in Patch {patch_index + 1} ({file_path})")