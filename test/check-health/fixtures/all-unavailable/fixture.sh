#!/usr/bin/env bash
# Empty machine: no PSI, no battery, no sensors, no readable mount.
# Everything is unavailable → overall collapses to "ok" (we don't paint a
# pill yellow because the box has no battery).
EXPECT_OVERALL=ok
EXPECT_WAYBAR_CLASS=ok
EXPECT_TEXT_SUBSTRING="[unavailable]"
