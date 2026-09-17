# Knowledge Server

This glossary defines the project-specific terms for the note collection and
its synchronized copies.

## Language

**Knowledge vault**:
The owner's authoritative collection of Markdown notes, versioned with Git.

**Vault remote**:
The NAS-hosted Git repository used as the synchronization source for local
copies of the knowledge vault. It has no checked-out working tree.
_Avoid_: Knowledge root, searchable vault

**Knowledge root**:
The local Git checkout of the knowledge vault designated as the current
retrieval copy. Its current contents may differ from its last commit.
_Avoid_: Vault remote, bare repository
