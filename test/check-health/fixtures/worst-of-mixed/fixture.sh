#!/usr/bin/env bash
# Aggregation check: memory ok, battery warning, cpu critical → critical wins.
# Regression guard for the _merge_worst logic.
EXPECT_OVERALL=critical
EXPECT_WAYBAR_CLASS=critical
EXPECT_TEXT_SUBSTRING="battery: BAT0 75% health"
