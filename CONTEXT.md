# Knowledge Server

This glossary defines the project-specific terms for the note collection, its
synchronized copies, and the roles of the people and agents who work on it.

## Language

**Knowledge vault**:
The vault owner's authoritative collection of Markdown notes, versioned with
Git.

**Vault remote**:
The NAS-hosted Git repository used as the synchronization source for local
copies of the knowledge vault. It has no checked-out working tree.
_Avoid_: Local vault checkout, searchable vault

**Local vault checkout**:
The local Git checkout of the knowledge vault that the server reads. It is
configured through `KNOWLEDGE_ROOT`, and the code calls it the root. Its
current contents may differ from its last commit. Shorten it to "local
checkout" only where the context is clear.
_Avoid_: Vault remote, bare repository

**Vault owner**:
The person who owns the knowledge vault, directs this project, and makes the
decisions that the documents leave open.
_Avoid_: User, when the role is meant; owner, without "vault"

**Coordinating agent**:
The agent responsible for completing a development task and validating the
result. It may delegate bounded work to other agents and is responsible to the
vault owner.
_Avoid_: Owner
