# Changelog

## 0.1.1
- Detect the "no permission for hamachid" error and show the fix in the status bar and log
- Peer tree now has Type, Proto, Remote Address, Alias and Raw columns
- Detail pane shows every parsed field for the selected network or peer
- Added [ Raw List ] and [ Peer Info ] (runs hamachi peer <ip>) buttons

## 0.1.0
- First version
- Status, login, logout, join, leave, create, go-online, go-offline
- Network and peer tree with right-click copy IP
- Auto refresh every 5 seconds in a worker thread
- Start Daemon button through pkexec
- Colored log in the standard format
- Optional throbber
