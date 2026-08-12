#!/usr/bin/env bash
PKG=shedos-test
RELPATH=.config/test/big.conf
EXPECT_SKIPPED=1
# Drive the oversized branch with a tiny threshold so the fixture stays small.
SHEDOS_FIXTURE_LARGE_FILE_LINES=5
