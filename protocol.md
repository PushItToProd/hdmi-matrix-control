# RS232 Protocol Notes

## Basic info

The manual for the PORTTA VALUE HDMI 4x2 Matrix, 4K30Hz Quad Multi Viewer provides the following info on the 4PET0402QMS's RS232 control protocol:

```
Baud Rate = 57600 bits per second as default
Data Bits = 8
Stop Bits = 1
Parity = None
Flow Control = None

Notes:

1. Carriage Return is required at end of each string
2. Commands are not case-sensitive. Spaces are shown for clarity: commands should NOT have any spaces
3. After a new command is received, a prompt should be sent back
4. HDMI Input selections via front button, IR remote, serial IR In, USB service port, trigger in, or RS-232 respond with the following message
   x = the currently selected input (1-4)
5. The response terminates with a carriage return followed by a line feed

Micro USB port used for configuration and control from third-party control terminals
Used for firmware updates
Supports USB driver for Windows 8.1/10/11, Mac OS 10.10 above. Will register as CDC Config Series Port in Device Manager. If the operation system of the PC is too old, the customers need to install the driver for CDC manually

Can be used as RS-232 control port
Baud rate is 57600
```

Two of the notes above don't hold for this unit.

Note 5 says "The response terminates with a carriage return followed by a line feed \[CRLF\]", which isn't true or useful for all commands:

- The `STA` and `H` commands use CRLF for all newlines, so just reading until the first CRLF won't get the full output.
- Commands sent to the HDMI matrix are echoed back, including the CRLF at the end, so we need to read at least two lines to get the full response.

Note 4 says input selections made from the front panel button, IR remote, serial IR in, the USB service port, or the trigger input emit a message on the serial port. This unit does not: only a command sent over RS232 produces output, and the port stays silent when an input is selected by any other means. Some firmware or hardware revisions of this chip may behave as the manual describes, so a reader that has to tolerate unsolicited output isn't wrong in general -- it just isn't needed here. What *is* needed is that a reply arriving later than expected doesn't get mistaken for the next command's reply; see "Command outputs" below.

## Commands

### Command outputs

- The characters sent to the HDMI matrix are echoed back, so each response starts with the command passed followed by CRLF.
- For the `STA` and `H` commands, we get a series of lines starting with `--` displaying status or help information, respectively. Other commands (which I call "basic commands") only return the line of output described below.
- For most commands, the response ends with a line of form `<s>{COMMAND}</s><user>{description}</user>`. (We'll call this the "command info line".)
  - `COMMAND` is the command we sent to the matrix with all letters in uppercase (even if we sent them in lowercase), unless the command we sent was invalid, in which case this value is a single question mark.
  - `description` is a human-readable description of what the command did.
  - See the "Basic commands" heading below for examples.
  - Exceptions:
    - `H` returns the help text listed below, but not the command info line.
    -  `SPOBCOPYOUTAON`/`SPOBCOPYOUTAOFF`

#### Help command (`H`)

```
-------------------------------------------------------------------------
--                             Systems HELP                            --
-------------------------------------------------------------------------
--  4PET0402QMS                                   F/W Version : 1.00   --
--                                                                     --
-- H: Help                                                             --
-- PF: Power Off                                                       --
-- PN: Power ON                                                        --
-- STA: Show Global System Status                                      --
--                                                                     --
-- Video Output Setup Commands: yy = [01-04,U,D],x = [A, B]            --
-- SPO x SI yy: Set Output x to Video Input yy                         --
-- SPO SI yy: Set Output A/B to Video Input yy                         --
-- SPO ON/OFF: Set Output ON/OFF                                       --
-- Set the Four Same Size Picture Mode for Four Combinations,          --
-- x=[1,2,3,4]                                                         --
-- SPOA 2x2 x: Set Output A to Four Video Input 2x2 mode x             --
-- Set the Two Picture Left/right Mode to x for Left Picture and y     --
-- for Right Picture; x=[1,2,3,4], y=[1,2,3,4]                         --
-- SPOA 2PLR x y: Set Output A to two Video Input Left x/right y mode  --
-- Set the Two Picture Up/down Mode to x for Up Picture and y for Down --
-- Picture; x=[1,2,3,4], y=[1,2,3,4]                                   --
-- SPOA 2PUD x y: Set Output A to two Video Input Up x/down y mode     --
-- Set the One Big Up Three Small Down Picture Mode for Four           --
-- Combinations, x=[1,2,3,4]                                           --
-- SPOA 1B3S x : Set Output A to Four Video Input 1B3S mode x          --
-- Set the Two Picture PIP Mode to x for Main Picture and y for Small  --
-- Picture; x=[1,2,3,4], y=[1,2,3,4]                                   --
-- SPOA PIP x y: Set Output A to two Video Input Main x/small y  PIP   --
-- mode                                                                --
-- SPOA PIP ROTATE: Set the PIP Mode Small Picture Location from Right --
-- Down Corner-Left Down corner- Left Up Corner-Right Up Corner        --
-- SPOA SCALER ROTATE: Set the  OutputA Resolution from                --
-- 4K30/2560x1600p/1080p circularly                                    --
-- SPOA RATIO ROTATE: Set the  OutputA RATIO Between Full Screen and   --
-- Keep the Original                                                   --
-- SPOB COPY OUTA ON/OFF: Set the  OutputB COPY the OutputA mode       --
-- On/Off                                                              --
--                                                                     --
-- Audio Output Setup Commands: [E=Enable, D=Disable]                  --
-- SPO A E/D: Enable/Disable External Optical and Analog Audio Output  --
-- SPO AM 2.1/5.1: Set the  Output Defaul Audio Mode to 2.1CH/5.1CH    --
-- Mode                                                                --
-- Set the OutputA Multi Picture Mode Audio Channel Selected Input x,  --
-- x=[1,2,3,4]                                                         --
-- SPOA A x: Set the  OutputA  Audio Channel to Input x                --
--                                                                     --
-- System Control Setup Commands:                                      --
-- SHOW OSD: Show the OSD Information and Disappear after 5s           --
-- SPC FB E/D: Enable/Disable Front Panel Buttons                      --
-- SPC RSB z: Set RS232 Baud Rate to z bps, z=[0-4]                    --
--              [0:57600, 1:38400, 2:19200, 3:9600, 4:4800]            --
-- SPC DF: Reset to Factory Defaults                                   --
----------
```


#### Status command (`STA`)

```
STA
-------------------------------------------------------------------------
--                           Systems STATUS                            --
-------------------------------------------------------------------------
-- 4PET0402QMS,                        Device Name: 4PET0402QMS_0001   --
--  F/W Version: 1.00                                                  --
-- Power : ON                                                          --
-- Front Panel Button : Enabled                                        --
-- RS232 : Baud Rate=57600bps, Data=8bit, Parity=None, Stop=1bit       --
--                                                                     --
-- Video Input 01 :  LINK = ON                                         --
-- Video Input 02 :  LINK = OFF                                        --
-- Video Input 03 :  LINK = OFF                                        --
-- Video Input 04 :  LINK = ON                                         --
--                                                                     --
-- Video Output: Output  = ON , DBG = ON                               --
-- Output A Video Mode: Input4 , RES = 1080p                           --
-- Output B Video Mode: Input1 , RES = 1920x1080p, COPY OUTA MODE = OFF--
--                                                                     --
-- Audio Output : Enabled                                              --
-- Audio Mode : 2.1CH                                                  --
-- Audio Input Channel : Input 4                                       --
-------------------------------------------------------------------------
<s>STA</s><user>Show Global System Status</user>
```

#### Basic commands

##### Set output B to input 1 (`spobsi01`)

```
spobsi01
<s>SPOBSI01</s><user>Set Output B to Video Input 01</user>
```

##### Set output B to input 4 (`spobsi04`)

```
spobsi04
<s>SPOBSI04</s><user>Set Output B to Video Input 04</user>
```

##### Two picture left/right mode (`spoa2plr14`)

```
spoa2plr14
<s>SPOA2PLR14</s><user>Set Output A to two Video Input Left 1/right 4 mode</user>
```

##### 2x2 mode (`spoa2x21`)

```
spoa2x21
<s>SPOA2X21</s><user>Set Output A to Four Video Input 2x2 mode 1</user>
```

##### Set output B to copy output A (`spobcopyoutaon`)

This and its companion `spobcopyoutaoff` are weird. Unlike most commands, these are non-idempotent and return "Unknow command" errors if output B is already in the mode that the command would put it in.

If we send `spobcopyoutaon` when `COPY OUTA MODE = OFF`, we get normal output:

```
spobcopyoutaon
<s>SPOBCOPYOUTAON</s><user>Set the OutputB COPY the OutputA mode On</user>
```

However, if `COPY OUTA MODE` is already `ON`, we get an "Unknow command" error:

```
spobcopyoutaon
<s>?</s><user>Unknow command</user>
```

##### Set output B to stop copying output A (`spobcopyoutaoff`)

This one is even weirder than `spobcopyoutaon`.

If `COPY OUTA MODE = ON` when we send `spobcopyoutaoff` to turn it off, we don't get a response back at all. However, output B does indeed stop copying output A. We can verify this by running `sta`.

As with `spobcopyoutaon`, if you run this command while `COPY OUTA MODE` is already `OFF`, you get back an "Unknow command" error:

```
spobcopyoutaoff
<s>?</s><user>Unknow command</user>
```

##### Unknown command response

This gets returned if the provided command doesn't exist or when commands are used in an invalid situation (see `spobcopyoutaon` and `spobcopyoutaoff` above).

Below is the output of sending `foo`, an invalid command. The `<s>` tag contains just a question mark, while the user-friendly description in `<user>` is "Unknow \[sic\] command". (Yes, "unknown" is misspelled in the actual `response`.)

```
foo
<s>?</s><user>Unknow command</user>
```