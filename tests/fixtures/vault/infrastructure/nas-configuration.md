# NAS Configuration

## Hardware

- **OS:** Unraid
- **Motherboard:** ASRock B450M Pro4-F
- **CPU:** AMD Ryzen 5 2600X
- **Memory:** 32 GB ECC UDIMM

## Storage

Storage is managed through Unraid shares.

Main shares include:

- `SSD_storage` — SSD-based storage for frequently accessed data
- `HDD_storage` — HDD-based storage for general and larger files

## Virtual Machines

Unraid is also used to host virtual machines.

The main development VM runs Ubuntu Server and is used for software development and server-side projects.

The Unraid shares can be mounted inside the VM when direct access to NAS storage is needed.

## Git Repositories

Bare Git repositories can be stored on the NAS for persistent storage.

For example:

    /mnt/user/SSD_storage/git/

Development should normally be done in a working clone inside the development VM rather than directly inside the NAS repository.

## Related

- [[Ubuntu Development Environment]]
- [[Knowledge System Architecture]]
