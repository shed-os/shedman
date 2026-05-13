# Simulate the user resolving the merge in VS Code by writing the
# merged content directly to the workspace file (no markers left).
cat > "$WORKSPACE/.config/hypr/hyprland.conf" <<'EOF'
monitor = , preferred, auto, 1
input { kb_layout = us, kb_options = caps:escape }
exec-once = waybar
exec-once = mako
EOF
