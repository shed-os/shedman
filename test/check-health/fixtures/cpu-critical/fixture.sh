#!/usr/bin/env bash
# Thermal runaway: max core temp 95°C (>= 90 crit).
EXPECT_OVERALL=critical
EXPECT_WAYBAR_CLASS=critical
EXPECT_TEXT_SUBSTRING="cpu: max 95°C"
