#!/bin/bash
# Completion e2e driver, run inside the e2e container.
#
# Expects the mounted staging dir at /work with:
#   /work/zsh_completion.zsh
#   /work/fish_completion.fish
#   /work/bin/oc-selector   (stub executable)
#   /work/bin/opencode      (stub --get-yargs-completions)
#
# Drives real zsh + fish via tmux and prints one "<shell> <case> PASS|FAIL"
# line per case plus a SUMMARY line. Exits 0 iff every case passed.
set -uo pipefail

PASS=0
FAIL=0

report() {
  local shell="$1" case_name="$2" status="$3"
  printf '%s %s %s\n' "$shell" "$case_name" "$status"
  if [ "$status" = "PASS" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
  fi
}

pane_contains() {
  local target="$1" token="$2"
  tmux capture-pane -t "$target" -p | grep -q -- "$token"
}

# --- zsh -------------------------------------------------------------------
cat > /tmp/zsh_setup.zsh <<'SETUP'
export PATH="/work/bin:$PATH"
autoload -Uz compinit
compinit -u 2>/dev/null
zmodload zsh/complist 2>/dev/null
zstyle ':completion:*' menu select
bindkey '^I' complete-word
source /work/zsh_completion.zsh
compdef _oc-selector_completions oc-selector
SETUP

tmux kill-server 2>/dev/null || true
tmux new-session -d -s z -x 120 -y 40 'zsh -f'
sleep 2
tmux send-keys -t z 'source /tmp/zsh_setup.zsh' Enter
sleep 1.5

tmux send-keys -t z 'oc-selector create ' Tab Tab
sleep 1
if pane_contains z -- '--model'; then report zsh create PASS; else report zsh create FAIL; fi
tmux send-keys -t z C-u; sleep 0.4

tmux send-keys -t z 'oc-selector run ' Tab Tab
sleep 1
if pane_contains z -- '--session'; then report zsh run PASS; else report zsh run FAIL; fi
tmux send-keys -t z C-u; sleep 0.4

tmux send-keys -t z 'oc-selector ' Tab Tab
sleep 1
if pane_contains z 'set-default' && tmux capture-pane -t z -p | grep -qw 'run'; then
  report zsh first PASS
else
  report zsh first FAIL
fi

# --- fish ------------------------------------------------------------------
tmux new-session -d -s f -x 120 -y 40 'fish'
sleep 2
tmux send-keys -t f 'set -gx PATH /work/bin $PATH' Enter
sleep 0.5
tmux send-keys -t f 'source /work/fish_completion.fish' Enter
sleep 1

tmux send-keys -t f 'oc-selector create ' Tab
sleep 1
if pane_contains f -- '--model'; then report fish create PASS; else report fish create FAIL; fi
tmux send-keys -t f C-c; sleep 0.5

tmux send-keys -t f 'oc-selector run ' Tab
sleep 1
if pane_contains f -- '--session'; then report fish run PASS; else report fish run FAIL; fi
tmux send-keys -t f C-c; sleep 0.5

tmux send-keys -t f 'oc-selector ' Tab
sleep 1
if pane_contains f 'set-default' && tmux capture-pane -t f -p | grep -qw 'run'; then
  report fish first PASS
else
  report fish first FAIL
fi

printf 'SUMMARY pass=%d fail=%d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
