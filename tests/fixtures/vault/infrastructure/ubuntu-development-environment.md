# Ubuntu Development Environment

The main development VM runs on the NAS (see [[NAS Configuration]]).
Most of my software projects live here, including the knowledge server.

I want this VM to be easy to rebuild. If something breaks badly, it should be
faster to create a new VM and follow this note than to debug the old one for a
whole evening.

## VM settings

| Setting | Value |
|---|---|
| OS | Ubuntu Server 24.04 LTS |
| vCPUs | 6 |
| Memory | 16 GB |
| Disk | 120 GB vdisk on `SSD_storage` |
| Network | Bridge `br0`, fixed IP via router DHCP reservation |
| Hostname | `devbox` |

Six vCPUs leaves the other cores for Unraid itself and the Docker
containers. Giving the VM all 12 threads made the NAS web UI slow while
compiling.

The vdisk is on the SSD share because builds and `uv sync` are much slower on
the HDD share.

## Initial setup

After installing Ubuntu Server with OpenSSH enabled:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y build-essential git curl ripgrep tmux htop
sudo timedatectl set-timezone Asia/Tokyo
```

Then the Git defaults:

```bash
git config --global init.defaultBranch main
git config --global pull.rebase true
```

`pull.rebase true` avoids merge commits when I pull on a machine that also
has local commits.

## SSH access

I connect from the laptop with an SSH config entry:

```
Host devbox
    HostName 192.168.1.50
    User dev
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
```

Password login is disabled in `/etc/ssh/sshd_config`:

```
PasswordAuthentication no
PermitRootLogin no
```

Restart with `sudo systemctl restart ssh` after changing it.

For editors, VS Code Remote SSH works well. The first connection installs the
VS Code server under `~/.vscode-server`, which can grow to a few GB over time.

## Tools

| Tool | Version | Installed with | Notes |
|---|---|---|---|
| Python | 3.14 | uv | Never use the system Python for projects |
| uv | latest | install script | `uv self update` to upgrade |
| Node.js | 22 LTS | nvm | Needed for some MCP tools |
| Docker | 27 | apt (Docker repo) | User added to `docker` group |
| ripgrep | 14 | apt | Also used by the knowledge server |
| GitHub CLI | 2.x | apt (GitHub repo) | `gh auth login` once |

### uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.14
```

Each project keeps its own `.venv`. I don't install packages globally.

### Node.js

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
nvm install --lts
```

### Docker

I followed the official Docker instructions for Ubuntu instead of the
`docker.io` package, because the Ubuntu package was too old for the compose
plugin.

```bash
sudo usermod -aG docker $USER
```

Log out and in again after this, otherwise `docker ps` still needs sudo.

## Mounting the NAS shares

The VM mounts the Unraid shares over NFS. The shares must have NFS export
enabled in the Unraid share settings first.

```bash
sudo apt install -y nfs-common
sudo mkdir -p /mnt/nas/ssd /mnt/nas/hdd
```

Lines in `/etc/fstab`:

```
192.168.1.10:/mnt/user/SSD_storage /mnt/nas/ssd nfs defaults,_netdev,noatime 0 0
192.168.1.10:/mnt/user/HDD_storage /mnt/nas/hdd nfs defaults,_netdev,noatime 0 0
```

`_netdev` makes systemd wait for the network before mounting. Without it, the
VM sometimes booted without the mounts.

Test without rebooting:

```bash
sudo mount -a
df -h | grep nas
```

## Git workflow

Bare repositories are on the NAS under `/mnt/user/SSD_storage/git/`. From
the VM they are visible as `/mnt/nas/ssd/git/`.

Create a new bare repository:

```bash
git init --bare /mnt/nas/ssd/git/new-project.git
```

Clone it into the home directory and work there:

```bash
git clone /mnt/nas/ssd/git/new-project.git ~/src/new-project
```

I keep all working clones under `~/src/`. Never edit files inside the bare
repository directly.

Some projects also have a GitHub remote. For those, `origin` is GitHub and
`nas` is the bare repository:

```bash
git remote add nas /mnt/nas/ssd/git/knowledge-server.git
git push nas main
```

## Directory layout

```
~/src/          working clones
~/scratch/      throwaway experiments, safe to delete
~/data/         local copies of datasets (not backed up)
/mnt/nas/ssd/   SSD share
/mnt/nas/hdd/   HDD share
```

`~/data/` is only for speed. The original datasets stay on the HDD share, so
losing `~/data/` is not a problem.

## Backups

The VM itself is not backed up as a whole. Instead:

- Code is in Git, pushed to the NAS and sometimes GitHub.
- Dotfiles are in a separate `dotfiles` repository.
- The vdisk can be recreated from this note.

Unraid's own backup of the SSD share covers the bare repositories.

What is not covered: uncommitted changes in `~/src/`, and anything in
`~/scratch/`. Commit or push before risky changes to the VM.

## Updates

Monthly, or before starting a big task:

```bash
sudo apt update && sudo apt upgrade -y
uv self update
sudo reboot
```

Check that the NFS mounts came back after the reboot with `df -h`.

## Things I tried and dropped

- **Dev containers for every project.** Too slow to start for small scripts,
  and the extra layer made SSH agent forwarding confusing.
- **Running the VM on the HDD share.** Builds were noticeably slower.
- **Mounting the shares with SMB.** File permissions were confusing, and
  symlinks behaved differently from NFS.
- **Using the system Python with `pip install --user`.** Version conflicts
  between projects.

## Troubleshooting

### The NAS share is not mounted

1. Check that the NAS itself is up and the share exists in the Unraid UI.
2. Check that NFS export is still enabled for the share. An Unraid update
   once reset it to off.
3. Run `sudo mount -a` and read the error message.
4. If the error is `access denied by server`, check the NFS rule for the
   share. It must allow the VM's IP address.

### SSH connection is refused

Usually the VM did not start after a NAS reboot. Start it from the Unraid VM
tab and enable autostart.

### `uv sync` is very slow

Check that the project is under `~/src/` and not on an NFS mount. Building
the `.venv` on NFS is much slower.

## TODO

- Write a script for the initial setup instead of copying commands.
- Decide whether to move Docker data to a separate vdisk.
- Try Tailscale for access from outside the home network.

## Related

- [[NAS Configuration]]
- [[Git Basics]]
- [[Knowledge System Architecture]]
