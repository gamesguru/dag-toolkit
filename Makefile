# DAG Merge & State Resolution Comparison
# Usage:
#   make merge ROOM=c10y-t1HZB9jgYr9mmaKtMDsS19HXbWRFc6d0bWGVYU-v12
#   make compare ROOM=c10y-t1HZB9jgYr9mmaKtMDsS19HXbWRFc6d0bWGVYU-v12
#   make compare-all

VERSION ?= v2-1
PREFIX = remote-dag

# Strip PREFIX from ROOM if user accidentally includes it
_ROOM = $(patsubst $(PREFIX)-%,%,$(ROOM))

# Collect all JSONL files for a given ROOM
files = $(wildcard $(PREFIX)-$(_ROOM)-*.jsonl)
inputs = $(foreach f,$(files),-i $(f))

.PHONY: merge cmp cmp-all list servers crawl timeline

# Merge all server DAGs for a room and output ground truth
merge:
	@test -n "$(ROOM)" || (echo "Usage: make merge ROOM=<room-slug>" && exit 1)
	@echo "Merging: $(files)"
	ruma-lean $(inputs) --state-res $(VERSION) -f default

# Compare servers vs merged ground truth.
# VERBOSE=1 for per-user diffs, RANK=1 to sort by F1 score.
cmp:
	@test -n "$(ROOM)" || (echo "Usage: make cmp ROOM=<room-slug>" && exit 1)
	@python3 dagcmp.py $(_ROOM) --prefix $(PREFIX) $(if $(VERBOSE),-v,) $(if $(RANK),-r,)

# Compare all rooms found in this directory
cmp-all:
	@for room in $$(ls $(PREFIX)-*.jsonl 2>/dev/null | sed 's/$(PREFIX)-//;s/-[^-]*\.jsonl//' | sort -u); do \
		echo "=== $$room ==="; \
		$(MAKE) --no-print-directory cmp ROOM="$$room" VERSION=$(VERSION); \
		echo; \
	done

# List available rooms and their server counts
list:
	@for room in $$(ls $(PREFIX)-*.jsonl 2>/dev/null | sed 's/$(PREFIX)-//;s/-[^-]*\.jsonl//' | sort -u); do \
		count=$$(ls $(PREFIX)-$$room-*.jsonl 2>/dev/null | wc -l); \
		printf "%-60s %s servers\n" "$$room" "$$count"; \
	done

# First N servers to join a room by depth order (default: 100)
N ?= 100
servers:
	@test -n "$(ROOM)" || (echo "Usage: make servers ROOM=<room-slug>" && exit 1)
	@cat $(files) | jq -r 'select(.type == "m.room.member" and .content.membership == "join") | "\(.depth)\t\(.state_key)"' \
		| sort -n | awk -F'[:@]' '{print $$3}' | awk '!seen[$$0]++' | head -$(N)

# Show servers in the DAG we haven't crawled yet, with ready-to-paste admin commands
ROOM_ID ?= $(shell cat $(firstword $(files)) | head -1 | jq -r '.room_id // empty' 2>/dev/null)
crawl:
	@test -n "$(ROOM)" || (echo "Usage: make crawl ROOM=<room-slug>" && exit 1)
	@have=$$(echo "$(files)" | tr ' ' '\n' | sed 's/.*-v[0-9]*-//;s/\.jsonl//' | sort -u); \
	all=$$(cat $(files) | jq -r 'select(.type == "m.room.member" and .content.membership == "join") | .state_key' \
		| awk -F'[:@]' '{print $$3}' | sort -u); \
	missing=$$(comm -23 <(echo "$$all") <(echo "$$have")); \
	n_have=$$(echo "$$have" | wc -l | tr -d ' '); \
	n_all=$$(echo "$$all" | wc -l | tr -d ' '); \
	n_missing=$$(echo "$$missing" | grep -c . 2>/dev/null || echo 0); \
	echo "Crawled: $$n_have / $$n_all servers ($$n_missing remaining)"; \
	if [ -n "$(ROOM_ID)" ]; then \
		echo; echo "# Paste into admin room:"; \
		echo "$$missing" | while read srv; do \
			echo "yolo get-remote-dag $(ROOM_ID) $$srv --limit -1"; \
		done; \
	fi

# Render merged DAG as a human-readable timeline
timeline:
	@test -n "$(ROOM)" || (echo "Usage: make timeline ROOM=<room-slug>" && exit 1)
	@ruma-lean -q $(inputs) --state-res $(VERSION) -f timeline 2>&1 | cat

