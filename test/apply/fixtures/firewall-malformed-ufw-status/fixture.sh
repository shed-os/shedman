#!/usr/bin/env bash
# A rule shape the parser doesn't know must not abort the plan: it is
# skipped with a note (removals go by live rule number, which an
# unparsed rule never gets, so skipping cannot mutate it).
EXIT_CODE=0
