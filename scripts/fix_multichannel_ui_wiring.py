#!/usr/bin/env python3
"""
Fix multi-channel EXR UI wiring:
1. Hide redundant mini-bar in SourcesPanel.
2. Synchronize both floating HUD and docked VFXEXRDockWidget on media load, layer switch, preset change, and solo toggles.
3. Automatically exit contact sheet mode when a pass or preset is clicked in the dock.
4. Auto-expand Layer Passes section in VFX EXR dock when a multi-channel EXR is loaded.
"""
import os

def patch_sources_panel():
    path = "src/openreview/ui/sources_panel.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Hide exr_properties_bar permanently in _update_details_for_target
    old_code = '''        if exr_path and hasattr(self, "exr_properties_bar"):
            try:
                from openreview.core.image_io import get_exr_channels
                ch_info = get_exr_channels(str(exr_path))
                num_layers = len(ch_info.get("layers", {}))
                self.lbl_exr_badge.setText(f"EXR ({num_layers} AOVs)" if num_layers > 1 else "EXR")
            except Exception:
                self.lbl_exr_badge.setText("EXR")
            self.exr_properties_bar.show()
        elif hasattr(self, "exr_properties_bar"):
            self.exr_properties_bar.hide()'''

    new_code = '''        # Unify in dedicated VFX EXR dock; keep redundant mini-bar hidden
        if hasattr(self, "exr_properties_bar"):
            self.exr_properties_bar.hide()'''

    assert old_code in content, "Failed to find exr_properties_bar visibility logic in sources_panel.py"
    content = content.replace(old_code, new_code, 1)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    print("Successfully patched sources_panel.py")

def patch_main_window():
    path = "src/openreview/ui/main_window.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update _load_path EXR block to populate vfx_exr_dock
    old_load = '''        if str(path).lower().endswith(".exr"):
            try:
                from openreview.core.image_io import get_exr_channels
                exr_info = get_exr_channels(str(path))
                lyrs = list(exr_info.get("layers", {}).keys())
                for cl in exr_info.get("cryptomatte_layers", []):
                    if cl not in lyrs:
                        lyrs.append(cl)
                if not lyrs:
                    lyrs = ["beauty"]
                self.top_bar.set_available_layers(lyrs, "beauty")
                if hasattr(self, "layers_widget"):
                    self.layers_widget.refresh_list(lyrs)
                try:
                    layers_dict, all_chans = inspect_exr_matrix(str(path))
                    if hasattr(self, "channel_matrix_widget"):
                        self.channel_matrix_widget.populate_matrix(layers_dict, all_chans)
                except Exception:
                    pass
            except Exception:
                self.top_bar.set_available_layers(["beauty"], "beauty")
                if hasattr(self, "layers_widget"):
                    self.layers_widget.refresh_list(["beauty"])
        else:
            self.top_bar.set_available_layers(["beauty"], "beauty")
            if hasattr(self, "layers_widget"):
                self.layers_widget.refresh_list(["beauty"])'''

    new_load = '''        if str(path).lower().endswith(".exr"):
            try:
                from openreview.core.image_io import get_exr_channels
                exr_info = get_exr_channels(str(path))
                lyrs = list(exr_info.get("layers", {}).keys())
                for cl in exr_info.get("cryptomatte_layers", []):
                    if cl not in lyrs:
                        lyrs.append(cl)
                if not lyrs:
                    lyrs = ["beauty"]
                self.top_bar.set_available_layers(lyrs, "beauty")
                if hasattr(self, "layers_widget"):
                    self.layers_widget.refresh_list(lyrs)
                if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
                    self.vfx_exr_dock.layers_widget.refresh_list(lyrs)
                    if len(lyrs) > 1 and hasattr(self.vfx_exr_dock, "sec_layers"):
                        self.vfx_exr_dock.sec_layers.set_expanded(True)

                try:
                    layers_dict, all_chans = inspect_exr_matrix(str(path))
                    if hasattr(self, "channel_matrix_widget"):
                        self.channel_matrix_widget.populate_matrix(layers_dict, all_chans)
                    if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "channel_matrix_widget"):
                        self.vfx_exr_dock.channel_matrix_widget.populate_matrix(layers_dict, all_chans)
                except Exception:
                    pass
            except Exception:
                self.top_bar.set_available_layers(["beauty"], "beauty")
                if hasattr(self, "layers_widget"):
                    self.layers_widget.refresh_list(["beauty"])
                if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
                    self.vfx_exr_dock.layers_widget.refresh_list(["beauty"])
        else:
            self.top_bar.set_available_layers(["beauty"], "beauty")
            if hasattr(self, "layers_widget"):
                self.layers_widget.refresh_list(["beauty"])
            if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
                self.vfx_exr_dock.layers_widget.refresh_list(["beauty"])
            if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "channel_matrix_widget"):
                self.vfx_exr_dock.channel_matrix_widget.populate_matrix({}, [])'''

    assert old_load in content, "Failed to find _load_path EXR block in main_window.py"
    content = content.replace(old_load, new_load, 1)

    # 2. Update _on_layer_changed to exit contact sheet mode and sync vfx_exr_dock
    old_lyr_changed = '''    def _on_layer_changed(self, layer_name: str) -> None:
        """Handle switching of multi-channel EXR layers."""
        self._current_exr_layer = layer_name
        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(layer_name)
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_current_layer(layer_name)
        if hasattr(self, "channel_matrix_widget"):
            self.channel_matrix_widget.apply_preset(layer_name)'''

    new_lyr_changed = '''    def _on_layer_changed(self, layer_name: str) -> None:
        """Handle switching of multi-channel EXR layers."""
        self._current_exr_layer = layer_name
        # Auto-exit Contact Sheet mode on pass selection
        if getattr(self, "_is_exr_contact_sheet_active", False):
            self._is_exr_contact_sheet_active = False
            if hasattr(self, "layers_widget"):
                self.layers_widget.set_contact_sheet_active(False)
            if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
                self.vfx_exr_dock.layers_widget.set_contact_sheet_active(False)

        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(layer_name)
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_current_layer(layer_name)
        if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
            self.vfx_exr_dock.layers_widget.set_current_layer(layer_name)
        if hasattr(self, "channel_matrix_widget"):
            self.channel_matrix_widget.apply_preset(layer_name)
        if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "channel_matrix_widget"):
            self.vfx_exr_dock.channel_matrix_widget.apply_preset(layer_name)'''

    assert old_lyr_changed in content, "Failed to find _on_layer_changed in main_window.py"
    content = content.replace(old_lyr_changed, new_lyr_changed, 1)

    # 3. Update _on_exr_matrix_mapping_changed to auto-exit contact sheet mode
    old_mapping = '''    def _on_exr_matrix_mapping_changed(self, mapping: Any) -> None:
        """Handle custom channel-to-RGBA routing from Channel Matrix HUD."""'''

    new_mapping = '''    def _on_exr_matrix_mapping_changed(self, mapping: Any) -> None:
        """Handle custom channel-to-RGBA routing from Channel Matrix HUD."""
        # Auto-exit contact sheet mode on custom channel swizzle
        if getattr(self, "_is_exr_contact_sheet_active", False):
            self._is_exr_contact_sheet_active = False
            if hasattr(self, "layers_widget"):
                self.layers_widget.set_contact_sheet_active(False)
            if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
                self.vfx_exr_dock.layers_widget.set_contact_sheet_active(False)'''

    assert old_mapping in content, "Failed to find _on_exr_matrix_mapping_changed in main_window.py"
    content = content.replace(old_mapping, new_mapping, 1)

    # 4. Update _on_toggle_exr_contact_sheet to sync vfx_exr_dock.layers_widget
    old_toggle_cs = '''    def _on_toggle_exr_contact_sheet(self, enabled: bool) -> None:
        """Toggle adaptive multi-channel EXR Contact Sheet mode."""
        self._is_exr_contact_sheet_active = enabled
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(enabled)'''

    new_toggle_cs = '''    def _on_toggle_exr_contact_sheet(self, enabled: bool) -> None:
        """Toggle adaptive multi-channel EXR Contact Sheet mode."""
        self._is_exr_contact_sheet_active = enabled
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(enabled)
        if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
            self.vfx_exr_dock.layers_widget.set_contact_sheet_active(enabled)'''

    assert old_toggle_cs in content, "Failed to find _on_toggle_exr_contact_sheet in main_window.py"
    content = content.replace(old_toggle_cs, new_toggle_cs, 1)

    # 5. Update _on_view_single_exr_pass and _on_exr_contact_sheet_tile_double_clicked to sync vfx_exr_dock
    old_view_single = '''        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(False)
            self.layers_widget.set_current_layer(target_pass)
        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(target_pass)'''

    new_view_single = '''        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(False)
            self.layers_widget.set_current_layer(target_pass)
        if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
            self.vfx_exr_dock.layers_widget.set_contact_sheet_active(False)
            self.vfx_exr_dock.layers_widget.set_current_layer(target_pass)
        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(target_pass)'''

    assert old_view_single in content, "Failed to find _on_view_single_exr_pass sync in main_window.py"
    content = content.replace(old_view_single, new_view_single, 1)

    old_tile_dc = '''        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(False)
            self.layers_widget.set_current_layer(layer_name)
        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(layer_name)'''

    new_tile_dc = '''        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(False)
            self.layers_widget.set_current_layer(layer_name)
        if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "layers_widget"):
            self.vfx_exr_dock.layers_widget.set_contact_sheet_active(False)
            self.vfx_exr_dock.layers_widget.set_current_layer(layer_name)
        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(layer_name)'''

    assert old_tile_dc in content, "Failed to find _on_exr_contact_sheet_tile_double_clicked sync in main_window.py"
    content = content.replace(old_tile_dc, new_tile_dc, 1)

    # 6. Update _on_show_exr_inspector to show and focus vfx_exr_dock
    old_show_insp = '''    def _on_show_exr_inspector(self) -> None:
        """Reveal and focus EXR Multilayer Pass Inspector floating HUD."""
        if hasattr(self, "hud_layers"):
            self.hud_layers.show()
            self.hud_layers.raise_()'''

    new_show_insp = '''    def _on_show_exr_inspector(self) -> None:
        """Reveal and focus EXR Multilayer Pass Inspector dock or HUD."""
        if hasattr(self, "vfx_exr_dock"):
            self.vfx_exr_dock.show()
            self.vfx_exr_dock.raise_()
        elif hasattr(self, "hud_layers"):
            self.hud_layers.show()
            self.hud_layers.raise_()'''

    assert old_show_insp in content, "Failed to find _on_show_exr_inspector in main_window.py"
    content = content.replace(old_show_insp, new_show_insp, 1)

    # 7. Update solo button sync in _on_exr_channel_mode_changed
    old_solo_sync = '''        if hasattr(self, "channel_matrix_widget"):
            btn = self.channel_matrix_widget._solo_btns.get(mode)
            if btn:
                btn.setChecked(True)'''

    new_solo_sync = '''        if hasattr(self, "channel_matrix_widget"):
            btn = self.channel_matrix_widget._solo_btns.get(mode)
            if btn:
                btn.setChecked(True)
        if hasattr(self, "vfx_exr_dock") and hasattr(self.vfx_exr_dock, "channel_matrix_widget"):
            btn = self.vfx_exr_dock.channel_matrix_widget._solo_btns.get(mode)
            if btn:
                btn.setChecked(True)'''

    assert old_solo_sync in content, "Failed to find _on_exr_channel_mode_changed solo button sync in main_window.py"
    content = content.replace(old_solo_sync, new_solo_sync, 1)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    print("Successfully patched main_window.py")

if __name__ == "__main__":
    patch_sources_panel()
    patch_main_window()
