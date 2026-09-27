#!/usr/bin/env bash
# List review threads for a PR as compact JSON, one object per thread.
# Usage: threads.sh <pr-number> [--all]
#   default: unresolved threads only   --all: every thread
# Run from inside the repo; gh fills {owner}/{repo} from the current directory.
set -euo pipefail

pr="${1:?usage: threads.sh <pr-number> [--all]}"
filter='select(.isResolved|not)'
[[ "${2:-}" == "--all" ]] && filter='.'

gh api graphql --paginate -F owner='{owner}' -F repo='{repo}' -F pr="$pr" -f query='
query($owner:String!,$repo:String!,$pr:Int!,$endCursor:String){
  repository(owner:$owner,name:$repo){ pullRequest(number:$pr){
    reviewThreads(first:100,after:$endCursor){ pageInfo{hasNextPage endCursor}
      nodes{ id isResolved isOutdated path line
        opener:comments(first:1){nodes{databaseId author{login} body}}
        latest:comments(last:1){nodes{databaseId author{login} body createdAt originalCommit{abbreviatedOid}}}
}}}}}' --jq ".data.repository.pullRequest.reviewThreads.nodes[] | $filter |
  (.opener.nodes[0].body // \"\") as \$ask | (.latest.nodes[0].body // \"\") as \$last | {
  thread: .id, resolved: .isResolved, outdated: .isOutdated, path, line,
  reply_to: .opener.nodes[0].databaseId,
  last_id: .latest.nodes[0].databaseId,
  opened_by: .opener.nodes[0].author.login,
  last_by: .latest.nodes[0].author.login,
  on_commit: .latest.nodes[0].originalCommit.abbreviatedOid,
  ask: \$ask[0:400],
  last: \$last[0:300],
  truncated: ((\$ask | length) > 400 or (\$last | length) > 300)
}"
