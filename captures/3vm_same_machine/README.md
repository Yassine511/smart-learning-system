# Manual browser and Wireshark observations

These screenshots were captured during the 19 October 2026 manual walkthrough
of the local three-VM prototype using synthetic student accounts.

| Scenario | Application view | Wireshark view |
| --- | --- | --- |
| Incorrect password: HTTP 401 | [Invalid login](invalid_login_app.png) | [POST and 401](invalid_login_wireshark.png) |
| Correct password: HTTP 200 | Successful login was confirmed in the browser | [POST and 200](valid_login_wireshark.png) |
| Create conversation: HTTP 201 | [Saved conversation](create_conv_app.png) | [POST and 201](create_conv_wireshark.png) |
| Send message: HTTP 200 with SSE | [Completed answer](send_msg_app.png) | [POST and text/event-stream](send_msg_wireshark.png) |

The readable HTTP capture is taken on VM1 loopback behind the HTTPS proxy, so
these packets show `127.0.0.1` as both endpoints. They demonstrate application
requests and responses; the separate TLS capture on the lab adapter is needed
to demonstrate the original client addresses. The successful-login image also
includes automated verification logins before the manual login.

These images show no request bodies, passwords, or session cookies. Raw captures
and credentials remain in ignored local storage. See the
[acceptance record](../../docs/local-three-vm-acceptance.md) for the automated
two-client results and observed stability limits. The manual two-client request
scenario had not yet been completed when these screenshots were committed.
