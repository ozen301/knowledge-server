# Knowledge Server

This glossary defines the project-specific terms for the note collection, its
synchronized copies, and the roles of the people and agents who work on it.

## Language

**Knowledge vault**:
The vault owner's authoritative collection of Markdown notes, versioned with
Git. The term comes from Obsidian, which calls a folder of notes a vault.

**Vault remote**:
The NAS-hosted Git repository used as the synchronization source for local
copies of the knowledge vault. It has no checked-out working tree.
_Avoid_: Local vault checkout, searchable vault

**Local vault checkout**:
A local Git checkout of the knowledge vault from which the server reads. Its
current contents may differ from its last commit. Shorten it to "local
checkout" only where the context is clear.
_Avoid_: Vault remote, bare repository

**Root**:
The directory that the server exposes: a local vault checkout or one subtree
of it, configured explicitly through `KNOWLEDGE_ROOT` for stdio or the HTTP
configuration file. Tool paths and citations are relative to it.
_Avoid_: Vault, when only the exposed directory is meant

**Inbox**:
The directory `inbox/` directly under the root, where the HTTP server saves
proposals. Git ignores it, and it is not part of the knowledge vault.

**Proposal**:
A new note or an edited copy of a note that an agent saved in the inbox. It
takes effect only when the vault owner merges it into the knowledge vault.
Each note has at most one pending proposal.
_Avoid_: Draft

**Review clone**:
A clone of the vault remote in which the vault owner reviews proposals with
`deploy/review-proposals`. The server never reads it.
_Avoid_: Local vault checkout, worktree

**Vault owner**:
The person who owns the knowledge vault, directs this project, and makes the
decisions that the documents leave open.
_Avoid_: User, when the role is meant; owner, without "vault"

**Coordinating agent**:
The agent responsible for completing a development task and validating the
result. It may delegate bounded work to other agents and is responsible to the
vault owner.
_Avoid_: Owner
