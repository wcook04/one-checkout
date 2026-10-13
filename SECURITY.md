# Security and limits

One Checkout is for trusted collaborators sharing a local Git checkout.
They still have filesystem and Git access. It is not a sandbox or protection
against a malicious collaborator.

Review the explicit patch, file paths, parent and commit message. A successful
commit establishes a particular Git tree; it does not establish that the
software works. Run the project's checks separately.

Git hooks are disabled for the tool's invocations, including commit and
reference-transaction hooks, without changing repository configuration.
Do not adopt it where policy depends on those hooks without a separate
validation path. Configured `commit.gpgSign` is honored; any signing program
must be available and trusted.

Keep patches free of secrets: their contents can become committed objects.
The tool does not inspect credentials or enforce organizational policy.
It does not push, publish, install hooks or change an agent's permissions.

The installer reads this package and writes only its selected skill
destinations, temporary staging directories, cooperating installer locks and
explicitly requested backups. Review local code before
installing it. Stable symlinks in source/destination ancestry and special files are refused.
The installer is not a hostile-filesystem sandbox; stop active skill use during
upgrades. Source hashes establish content identity, not publisher authenticity.

No public vulnerability-reporting destination is configured yet. Please
avoid putting secrets or a live repository into a reproduction.

Individual Git commands and complete protocol acknowledgements have configurable
deadlines. Signers run in owned child process groups; malicious helpers and
uninterruptible OS calls cannot be bounded by a normal process timeout. Partial
and promisor repositories are refused before object lookup to prevent implicit
lazy fetching. Accepted repositories still require trusted Git configuration.

Ref publication and index settlement are separate. Lost output or a killed
process cannot establish non-landing. Recovery information is observational:
never automatically retry, roll back a branch or steal a lock. Keep recovery
records private; they can contain local paths, object identities and error text.
