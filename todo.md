- [x] use `Serial` class as context manager

- [x] write a script that takes a raw command as its command line argument and emits the response -- e.g. `main.py sta`, `main.py h`, `main.py spobsi04`

- [ ] implement a `MultiviewerControl` class that generates and sends commands

- [ ] parse output of `STA` to get active inputs

- [ ] design a control server that keeps track of the system state
  - [ ] support swapping A and B with one command
  - [ ] rate limit request sending and ensure responses are handled cleanly -- when I ran `get_responses.sh` without `sleep` commands, the commands' output wasn't properly captured. (e.g. some output files were empty, while other output files contained responses from multiple commands)

## Possible `MultiviewerControl` class format

```python
c = MultiviewerControl()

# switch outputs to a single input
c.switch(output='A', input=4)
c.switch(output='B', input=1)
c.switch(output='both', input=2)

# or maybe this:
c.switch_a(input=4)
c.switch_b(input=1)
c.switch_both(input=2)

# 2x2 mode (n denotes which order to use as documented in the manual)
c.mode_2x2(n=1)

# left/right mode
c.split_vertical(left=2, right=3)

# up/down mode
c.split_horizontal(top=1, bottom=4)

# TODO: other modes
```
