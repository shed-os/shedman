# fake-code runs the action once with WORKSPACE set to the staging
# root. With the SCM-driven flow every file is read from the workspace
# path on close, so the action writes the resolved content at each
# file's workspace-rooted path.

cat > "$WORKSPACE/.config/hypr/hyprland.conf" <<'EOF'
monitor = , preferred, auto, 1
input { kb_layout = us, kb_options = caps:escape }
exec-once = waybar
exec-once = mako
EOF

cat > "$WORKSPACE/.config/kitty/kitty.conf" <<'EOF'
font_size 12.0
enable_audio_bell no
cursor_blink_interval 0.5
EOF
