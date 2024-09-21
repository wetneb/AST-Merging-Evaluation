#!/usr/bin/env sh

# usage: <scriptname> <clone_dir> <branch-1> <branch-2>

MERGE_SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd -P)"
clone_dir=$1
branch1=$2
branch2=$3
git_strategy="-s recursive -Xignore-space-change"
plumelib_strategy=""
"$MERGE_SCRIPTS_DIR"/merge_git_then_plumelib.sh "$clone_dir" "$branch1" "$branch2" "$git_strategy" "$plumelib_strategy"
