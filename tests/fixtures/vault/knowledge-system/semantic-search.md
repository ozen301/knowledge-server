# Semantic Search

Literal search only works when I remember the wording used in the note.
Semantic search could find notes by meaning instead.

## Basic idea

- Split notes into chunks
- Convert each chunk into an embedding vector
- Search by vector similarity

## FAISS

FAISS is a library for efficient similarity search of dense vectors.

Copied from the paper:

> Jeff Johnson, Matthijs Douze, Hervé Jégou. Billion-scale similarity search
> with GPUs.

Probably overkill for a personal vault. A few thousand notes could be
searched by brute force.

## Questions

- Which embedding model works for both English and Japanese?
- Should chunks follow Markdown headings?

## Related

- [[Model Context Protocol (MCP)]]
- [[Knowledge System Architecture]]
