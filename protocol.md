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

Although the notes above state "The response terminates with a carriage return followed by a line feed \[CRLF\]", this isn't necessarily true/useful for all commands: 

- The `STA` and `H` commands use CRLF for all newlines, so just reading until the first CRLF won't get the full output.
- Commands sent to the HDMI matrix are echoed back, including the CRLF at the end, so we need to read at least two lines to get the full response.

## Commands

Sample output of `H` (help) command listing all commands: (Extra newlines omitted for brevity)

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

### Sample command output

Sample output of the `STA` (status) command: (Extra newlines omitted for brevity.)

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

Output of `spobsi04` (set output B to input 4):

```
spoBsi01

<s>SPOBSI01</s><user>Set Output B to Video Input 01</user>

```

Output of `sbobsi01` (set output B to input 1):

```
spobsi04

<s>SPOBSI04</s><user>Set Output B to Video Input 04</user>

```
