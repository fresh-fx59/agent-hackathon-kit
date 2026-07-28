import sys

USAGE = """usage: python -m logalyzer <command> [options]
commands:
  stats          --logs <dir|zip>                     ingest summary
  suggest-repos  [--from <path>]                      find candidate code dirs
  investigate    --logs <dir|zip> --correlation-id <id>
                 [--repo <path>]... [--mode auto|dev|ops]
                 [--out report.json] [--md report.ru.md]
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
    print("unknown command: %s" % cmd); print(USAGE); return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
