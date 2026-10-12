"""Emit the tail of a build/test log as GitHub ::error:: workflow annotations.

The job logs of a public repository need a login to read, but check-run annotations do not
(GET /repos/<owner>/<repo>/check-runs/<job_id>/annotations), so a failing step calls this to make its own failure
readable. Limits: 10 error annotations per step and 50 per job, so the tail goes out as a handful of annotations of
LINES_PER_NOTE lines each, plus one annotation holding the last lines that look like errors.

usage: annotate_log_tail.py <title> <logfile> [tail_lines=40]
"""
import re
import sys

LINES_PER_NOTE = 10
MAX_LINE = 400
ERRORISH = re.compile(r"(error|fail|fatal|segmentation|traceback|assert|no such file|cannot|undefined reference)", re.I)


def escape_data(s):
    # the escaping that workflow commands require in the message part
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_prop(s):
    return escape_data(s).replace(":", "%3A").replace(",", "%2C")


def emit(title, body):
    print(f"::error title={escape_prop(title)}::{escape_data(body)}")


def main():
    title, path = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\r\n")[:MAX_LINE] for ln in f]
    except OSError as e:
        emit(title, f"no log to show: {e}")
        return
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        emit(title, "the log is empty")
        return
    tail = lines[-n:]
    chunks = [tail[i:i + LINES_PER_NOTE] for i in range(0, len(tail), LINES_PER_NOTE)]
    for i, chunk in enumerate(chunks, 1):
        emit(f"{title} - log tail {i}/{len(chunks)}", "\n".join(chunk))
    hits = [ln for ln in lines[:-n] if ERRORISH.search(ln)][-LINES_PER_NOTE:]
    if hits:
        emit(f"{title} - earlier error-like lines", "\n".join(hits))


if __name__ == "__main__":
    main()
