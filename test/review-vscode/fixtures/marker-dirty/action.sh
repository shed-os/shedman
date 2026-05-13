# Simulate an "abandoned" merge — the user touched the file but left
# conflict markers in. Tool must detect markers and refuse copy-back.
cat > "$WORKSPACE/.config/hypr/hyprland.conf" <<'EOF'
monitor = , preferred, auto, 1
<<<<<<< YOURS
input { kb_layout = us, kb_options = caps:escape }
=======
input { kb_layout = us }
>>>>>>> THEIRS
exec-once = waybar
EOF
