import sys

USAGE = """usage: python -m logalyzer <command> [options]
commands:
  stats            --logs <dir|zip>                     ingest summary (JSON;
                                                          "needs_inference" lists
                                                          files an unknown dialect
                                                          blocked -- always inline,
                                                          exit 0)
  suggest-repos    [--from <path>]                       find candidate code dirs
  investigate      --logs <dir|zip>
                   (--correlation-id <id> | --since <ISO> --until <ISO> |
                    --around <ISO> [--window <dur>])
                   [--service <name>] [--repo <path>]... [--mode auto|dev|ops]
                   [--out report.json] [--md report.ru.md]
                   correlation basis is exactly ONE of the three forms above;
                   --window duration like 5m/90s/1h, default 5m; --service
                   filters the time-window forms only (Normalization v2)
                   exit codes: 0 ok, 2 bad args (incl. zero or 2+ correlation
                               bases given), 3 needs --mode/--repo (ask),
                               4 an unrecognized log format left the timeline
                                 empty -- format_inference_needed JSON on
                                 stdout (fingerprint + masked sample lines +
                                 instructions); resolve with register-format
                                 and re-run
  register-format  <descriptor.json> --fingerprint <fp>
                   (--sample <file> | --sample-from-stats <stats.json>)
                   validate a {line_regex, ts_format} descriptor against the
                   sample (hit-rate thresholds) and, on success, learn it for
                   that fingerprint (zero-LLM cache hit on future ingests)
                   exit codes: 0 saved, 1 read/validation failed, 2 bad args
"""

def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE); return 2
    cmd = argv[0]
    if cmd == "stats":
        from logalyzer.cli_impl import cmd_stats; return cmd_stats(argv[1:])
    if cmd == "suggest-repos":
        from logalyzer.cli_impl import cmd_suggest; return cmd_suggest(argv[1:])
    if cmd == "investigate":
        from logalyzer.cli_impl import cmd_investigate; return cmd_investigate(argv[1:])
    if cmd == "register-format":
        from logalyzer.cli_impl import cmd_register_format; return cmd_register_format(argv[1:])
    print("unknown command: %s" % cmd); print(USAGE); return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
