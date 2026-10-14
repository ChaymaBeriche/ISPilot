#!/bin/sh
# Installs each requirement in <file> one at a time. This build environment
# reproducibly corrupts mid-download on anything past a few hundred KB (SSL
# record layer failures) — confirmed not tied to one specific file/version,
# so pip's own --retries (which only covers failed *connection attempts*,
# not a stream that broke after it was already open) can't route around it.
#
# Strategy per package:
#   1. A few quick plain `pip install` attempts — handles small packages,
#      which download in well under a second and rarely hit the corruption.
#   2. If those keep failing, fall back to pip_wheel_fetch.py, which
#      downloads the wheel via HTTP Range requests (resuming from wherever
#      the last attempt died) instead of gambling on one unbroken stream,
#      then installs the local file with --no-index.
set -e
file="$1"
plain_attempts=3
extra_index=""

install_via_resume() {
    spec="$1"
    pkg=$(printf '%s' "$spec" | sed -E 's/[<>=!~ ].*$//')
    ver=$(printf '%s' "$spec" | sed -E 's/^[^=]*==//')
    if [ -z "$pkg" ] || [ -z "$ver" ] || [ "$pkg" = "$ver" ]; then
        echo "cannot parse '$spec' as pkg==version for resume fallback" >&2
        return 1
    fi
    index_base=$(printf '%s' "$extra_index" | sed -E 's/^--extra-index-url[ ]+//')
    if [ -n "$index_base" ]; then
        path=$(python3 /app/pip_wheel_fetch.py "$pkg" "$ver" /tmp/wheelcache "$index_base") || return 1
    else
        path=$(python3 /app/pip_wheel_fetch.py "$pkg" "$ver" /tmp/wheelcache) || return 1
    fi
    # Install the exact local file directly (not `--find-links` + a name
    # spec) — pip prefers a fresh index download over a find-links match
    # even when both resolve to the same version, so --find-links alone
    # doesn't stop it from re-hitting the same broken network path. Passing
    # the literal path guarantees THIS package installs from disk, while
    # its own not-yet-installed dependencies (e.g. langchain-core for
    # langgraph-checkpoint) still resolve over the network normally — which
    # can itself cascade into several more downloads, any one of which can
    # hit the same corruption. Retry the whole cascade a few times too: pip
    # caches whatever it already downloaded successfully, so each retry only
    # has to get past whichever package broke last time, not start over.
    j=1
    while [ "$j" -le 5 ]; do
        if pip install --default-timeout=60 --retries=3 $extra_index "$path"; then
            return 0
        fi
        echo "dependency cascade for $spec failed (attempt $j/5), retrying..." >&2
        j=$((j + 1))
        sleep 2
    done
    return 1
}

while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|'#'*) continue ;;
        --extra-index-url*)
            extra_index="$line"
            continue
            ;;
    esac

    ok=0
    i=1
    while [ "$i" -le "$plain_attempts" ]; do
        if pip install --default-timeout=60 --retries=3 $extra_index "$line"; then
            ok=1
            break
        fi
        echo "pip install $line failed (attempt $i/$plain_attempts), retrying..." >&2
        i=$((i + 1))
        sleep 2
    done

    if [ "$ok" -ne 1 ]; then
        echo "plain pip install exhausted for $line — falling back to resumable download" >&2
        if install_via_resume "$line"; then
            ok=1
        fi
    fi

    if [ "$ok" -ne 1 ]; then
        echo "pip install $line failed after all strategies" >&2
        exit 1
    fi
done < "$file"
