# HamachiWrapper

Simple PyQt6 GUI around the `hamachi` command line client. Version 0.1.1.

## Setup (Arch)

```
yay -S logmein-hamachi
sudo systemctl enable --now logmein-hamachi
sudo pacman -S python-pyqt6
```

Or with pip:

```
pip install -r requirements.txt --break-system-packages
```

## Run

```
python3 hamachi_wrapper.py
```

## Buttons

- [ Login ] / [ Logout ]: hamachi login and logout
- [ Refresh ]: re-run status and list (also runs every 5 seconds)
- [ Start Daemon ]: runs `pkexec systemctl start logmein-hamachi`
- [ Join ] / [ Leave ] / [ Create ]: use the Network ID and Pass fields
- [ Go Online ] / [ Go Offline ]: toggle a network
- Click a network in the tree to fill the Network ID field
- Right-click a peer to copy its IP

## Log format

```
[{timestamp}] [HamachiWrapper] [{type}]: {command}
```

Passwords are masked in the log.
