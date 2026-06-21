---
source_file: NMEA0183_Protocol.pdf
last_synced: 2026-06-21 10:05
tags: [notion-sync, attachment]
---

# NMEA0183_Protocol

The NMEA 0183 Protocol

Table of Contents

1. What is the NMEA 0183 Standard? . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . page 1
Electrical Interface . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 2
2.
3. General Sentence Format
. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 2
Talker Identifiers . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 3
4.
Sentence Identifiers and Sentence Formats . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 4
5.
6.
Some Proprietary Sentences . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 21
7. Manufacturer Codes . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 25
8. References . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 28

The material presented in this document has been compiled from various inofficial sources. It is neither a
complete nor error-free description of the NMEA 0183 standard. In particular, it does not cover the new
sentences and the high-speed interface defined in version 3.x.

Klaus Betke, May 2000. Revised August 2001.

1. What is the NMEA 0183 Standard?

The National Marine Electronics Association (NMEA) is a non-profit association of manufacturers,
distributors, dealers, educational institutions, and others interested in peripheral marine electronics
occupations. The NMEA 0183 standard defines an electrical interface and data protocol for
communications between marine instrumentation.

NMEA 0183 is a voluntary industry standard, first released in March of 1983. It has been updated from
time to time; the latest release, currently (August 2001) Version 3.0, July 2001, is available from the
NMEA office (Warning: the price for non-members is 250 US$).

P O Box 3435
New Bern NC 28564-3435
USA
www.nmea.org

NMEA has also established a working group to develop a new standard for data communications among
shipboard electronic devices. The new standard, NMEA 2000, is a bi-directional, multi-transmitter,
multi-receiver serial data network. It is multi-master and self-configuring, and there is no central controller.
The NMEA began a beta testing period in January 2000 with eleven manufacturers. A release version of
NMEA 2000 is expected in 2001.

The NMEA 0183 Protocol

2. Electrical Interface

2

NMEA 0183 devices are designated as either talkers or listeners (with some devices being both),
employing an asynchronous serial interface with the following parameters: 

Baud rate:
Number of data bits:
Stop bits:
Parity:
Handshake:

4800
8 (bit 7 is 0)
1 (or more)
none
none

NMEA 0183 allows a single talker and several listeners on one circuit. The recommended interconnect
wiring is a shielded twisted pair, with the shield grounded only at the talker. The standard  dos not specify
the use of a particular connector. Note: The new 0183-HS standard  (HS = high speed)  introduced in
version 3.0 uses a 3-wire interface and a baud rate of 38400. This type of interface is not discussed here.

Its is recommended that the talker output comply with EIA RS-422, a differential system with two signal
lines, "A" and "B". Differential drive signals have no reference to ground and are more immune to noise.
However, a single-ended line at TTL level is accepted as well. The voltages on the A line correspond to
those on the TTL single wire, while the B voltages are inverted (when output A is at +5 V, output B is at
0 V, and vice versa. This is the unipolar RS-422 operation. In bipolar mode ±5 V are used).

In either case, the recommended receive circuit uses an opto-isolator with suitable protection circuitry.
The input should be isolated from the receiver's ground. In practice, the single wire, or the RS-422 "A"
wire may be directly connected to a computer's RS-232 input. In fact even many of the latest products,
like hand-held GPS receivers, do not have a RS-422 differential output, but just a single line with  TTL or
5 V CMOS compatible signal level.

3. General Sentence Format

All data is transmitted in the form of sentences. Only printable ASCII characters are allowed, plus CR
(carriage return) and LF (line feed). Each sentence starts with a "$" sign and ends with <CR><LF>. There
are three basic kinds of sentences: talker sentences, proprietary sentences and query sentences.

Talker Sentences. The general format for a talker sentence is: 

$ttsss,d1,d2,....<CR><LF> 

The first two letters following the „$” are the talker identifier. The next three characters (sss) are the
sentence identifier, followed by a number of data fields separated by commas, followed by an optional
checksum, and terminated by carriage return/line feed. The data fields are uniquely defined for each
sentence type. An example talker sentence is:

$HCHDM,238,M<CR><LF> 

where "HC" specifies the talker as being a magnetic compass, the "HDM" specifies the magnetic heading
message follows. The "238" is the heading value, and "M" designates the heading value as magnetic.

A sentence may contain up to 80 characters plus "$" and CR/LF. If data for a field is not available, the
field is omitted, but the delimiting commas are still sent, with no space between them. The checksum
field consists of a "*" and two hex digits representing the exclusive OR of all characters between, but not
including, the "$" and "*". 

Proprietary Sentences. The standard allows individual manufacturers to define proprietary sentence
formats. These sentences start with "$P", then a 3 letter manufacturer ID, followed by whatever data the
manufacturer wishes, following the general format of the standard sentences. Some proprietary
sentences, mainly from Garmin, Inc., are listed in chapter 6.

The NMEA 0183 Protocol

3

Query sentences. A query sentence is a means for a listener to request a particular sentence from a
talker. The general format is:

$ttllQ,sss,[CR][LF] 

The first two characters of the address field are the talker identifier of the requester and the next two
characters are the talker identifier of the device being queried (listener). The fifth character is always a "Q"
defining the message as a query. The next field (sss) contains the three letter mnemonic of the sentence
being requested. An example query sentence is:

$CCGPQ,GGA<CR><LF>

where the "CC" device (computer) is requesting from the "GP" device (a GPS unit) the "GGA" sentence.
The GPS will then transmit this sentence once per second until a different query is requested.

4. Talker Identifiers

Autopilot - General
Autopilot - Magnetic
Communications – Digital Selective Calling (DSC)
Communications – Receiver / Beacon Receiver
Communications – Satellite
Communications – Radio-Telephone (MF/HF)
Communications – Radio-Telephone (VHF)
Communications – Scanning Receiver
Direction Finder
Electronic Chart Display & Information System (ECDIS)
Emergency Position Indicating Beacon (EPIRB)
Engine Room Monitoring Systems
Global Positioning System (GPS)
Heading – Magnetic Compass
Heading – North Seeking Gyro

AG
AP
CD
CR
CS
CT
CV
CX
DF
EC
EP
ER
GP
HC
HE
HN    Heading – Non North Seeking Gyro
Integrated Instrumentation
II
Integrated Navigation
IN
Loran C
LC
Proprietary Code
P
RADAR and/or ARPA
RA
Sounder, Depth
SD
Electronic Positioning System, other/general
SN
Sounder, Scanning
SS
Turn Rate Indicator
TI
Velocity Sensor, Doppler, other/general
VD
Velocity Sensor, Speed Log, Water, Magnetic
DM
Velocity Sensor, Speed Log, Water, Mechanical
VW
Weather Instruments 
WI
Transducer
YX
Timekeeper – Atomic Clock
ZA
Timekeeper – Chronometer
ZC
Timekeeper – Quartz
ZQ
Timekeeper – Radio Update, WWV or WWVH
ZV

The NMEA 0183 Protocol

4

5. Sentence Identifiers and Formats

AAM Waypoint Arrival Alarm

       1 2 3   4 5    6
       | | |   | |    |
$--AAM,A,A,x.x,N,c--c*hh

1) Status, BOOLEAN, A = Arrival circle entered
2) Status, BOOLEAN, A = perpendicular passed at waypoint
3) Arrival circle radius
4) Units of radius, nautical miles
5) Waypoint ID
6) Checksum

ALM GPS Almanac Data

       1   2   3  4   5  6    7  8    9    10     11     12     13     14  15   16
       |   |   |  |   |  |    |  |    |    |      |      |      |      |   |    |
$--ALM,x.x,x.x,xx,x.x,hh,hhhh,hh,hhhh,hhhh,hhhhhh,hhhhhh,hhhhhh,hhhhhh,hhh,hhh,*hh

 1) Total number of messages
 2) Message Number
 3) Satellite PRN number (01 to 32)
 4) GPS Week Number: Date and time in GPS is computed as number of weeks
    from 6 January 1980 plus number of seconds into the week.
 5) SV health, bits 17-24 of each almanac page
 6) Eccentricity
 7) Almanac Reference Time
 8) Inclination Angle
 9) Rate of Right Ascension
10) Root of semi-major axis
11) Argument of perigee
12) Longitude of ascension node
13) Mean anomaly
14) F0 Clock Parameter
15) F1 Clock Parameter
16) Checksum

APA  Autopilot Sentence "A"

       1 2  3   4 5 6 7  8  9 10    11
       | |  |   | | | |  |  | |     |
$--APA,A,A,x.xx,L,N,A,A,xxx,M,c---c*hh

 1) Status
    V = LORAN-C Blink or SNR warning
    A = general warning flag or other navigation systems when a reliable
        fix is not available
 2) Status
    V = Loran-C Cycle Lock warning flag
    A = OK or not used
 3) Cross Track Error Magnitude
 4) Direction to steer, L or R
 5) Cross Track Units (Nautic miles or kilometres)
 6) Status
    A = Arrival Circle Entered
 7) Status
    A = Perpendicular passed at waypoint
 8) Bearing origin to destination
 9) M = Magnetic, T = True
10) Destination Waypoint ID
11) checksum

The NMEA 0183 Protocol

5

Autopilot Sentence "B"

APB
                                        13    15
       1 2 3   4 5 6 7 8   9 10   11  12|   14|
       | | |   | | | | |   | |    |   | |   | |
$--APB,A,A,x.x,a,N,A,A,x.x,a,c--c,x.x,a,x.x,a*hh

 1) Status
    V = LORAN-C Blink or SNR warning
    A = general warning flag or other navigation systems when a reliable
        fix is not available
 2) Status
    V = Loran-C Cycle Lock warning flag
    A = OK or not used
 3) Cross Track Error Magnitude
 4) Direction to steer, L or R
 5) Cross Track Units, N = Nautical Miles
 6) Status
    A = Arrival Circle Entered
 7) Status
    A = Perpendicular passed at waypoint
 8) Bearing origin to destination
 9) M = Magnetic, T = True
10) Destination Waypoint ID
11) Bearing, present position to Destination
12) M = Magnetic, T = True
13) Heading to steer to destination waypoint
14) M = Magnetic, T = True
15) Checksum

ASD

Autopilot System Data

Format unknown

Bearing & Distance to Waypoint – Dead Reckoning

BEC
                                                        12
       1         2       3 4        5 6   7 8   9 10  11|    13
       |         |       | |        | |   | |   | |   | |    |
$--BEC,hhmmss.ss,llll.ll,a,yyyyy.yy,a,x.x,T,x.x,M,x.x,N,c--c*hh

 1) Time (UTC)
 2) Waypoint Latitude
 3) N = North, S = South
 4) Waypoint Longitude
 5) E = East, W = West
 6) Bearing, True
 7) T = True
 8) Bearing, Magnetic
 9) M = Magnetic
10) Nautical Miles
11) N = Nautical Miles
12) Waypoint ID
13) Checksum

The NMEA 0183 Protocol

6

BOD

Bearing – Waypoint to Waypoint

       1   2 3   4 5    6    7
       |   | |   | |    |    |
$--BOD,x.x,T,x.x,M,c--c,c--c*hh

1) Bearing Degrees, TRUE
2) T = True
3) Bearing Degrees, Magnetic
4) M = Magnetic
5) TO Waypoint
6) FROM Waypoint
7) Checksum

BWC Bearing and Distance to Waypoint – Latitude, N/S, Longitude, E/W, UTC, Status

                                                      11
       1         2       3 4        5 6   7 8   9 10  | 12   13
       |         |       | |        | |   | |   | |   | |    |
$--BWC,hhmmss.ss,llll.ll,a,yyyyy.yy,a,x.x,T,x.x,M,x.x,N,c--c*hh

 1) Time (UTC)
 2) Waypoint Latitude
 3) N = North, S = South
 4) Waypoint Longitude
 5) E = East, W = West
 6) Bearing, True
 7) T = True
 8) Bearing, Magnetic
 9) M = Magnetic
10) Nautical Miles
11) N = Nautical Miles
12) Waypoint ID
13) Checksum

BWR Bearing and Distance to Waypoint – Rhumb Line Latitude, N/S, Longitude, E/W,

UTC, Status

                                                      11
       1         2       3 4        5 6   7 8   9 10  | 12   13
       |         |       | |        | |   | |   | |   | |    |
$--BWR,hhmmss.ss,llll.ll,a,yyyyy.yy,a,x.x,T,x.x,M,x.x,N,c--c*hh

 1) Time (UTC)
 2) Waypoint Latitude
 3) N = North, S = South
 4) Waypoint Longitude
 5) E = East, W = West
 6) Bearing, True
 7) T = True
 8) Bearing, Magnetic
 9) M = Magnetic
10) Nautical Miles
11) N = Nautical Miles
12) Waypoint ID
13) Checksum

The NMEA 0183 Protocol

7

BWW Bearing – Waypoint to Waypoint

       1   2 3   4 5    6    7
       |   | |   | |    |    |
$--BWW,x.x,T,x.x,M,c--c,c--c*hh

1) Bearing Degrees, TRUE
2) T = True
3) Bearing Degrees, Magnetic
4) M = Magnetic
5) TO Waypoint
6) FROM Waypoint
7) Checksum

DBK

Depth Below Keel

       1   2 3   4 5   6 7
       |   | |   | |   | |
$--DBK,x.x,f,x.x,M,x.x,F*hh

1) Depth, feet
2) f = feet
3) Depth, meters
4) M = meters
5) Depth, Fathoms
6) F = Fathoms
7) Checksum

DBS

Depth Below Surface

       1   2 3   4 5   6 7
       |   | |   | |   | |
$--DBS,x.x,f,x.x,M,x.x,F*hh

1) Depth, feet
2) f = feet
3) Depth, meters
4) M = meters
5) Depth, Fathoms
6) F = Fathoms
7) Checksum

DBT

Depth Below Transducer

       1   2 3   4 5   6 7
       |   | |   | |   | |
$--DBT,x.x,f,x.x,M,x.x,F*hh

1) Depth, feet
2) f = feet
3) Depth, meters
4) M = meters
5) Depth, Fathoms
6) F = Fathoms
7) Checksum

DCN

Decca Position

obsolete

The NMEA 0183 Protocol

8

DPT

Heading – Deviation & Variation

       1   2   3
       |   |   |
$--DPT,x.x,x.x*hh

1) Depth, meters
2) Offset from transducer;
   positive means distance from transducer to water line,
   negative means distance from transducer to keel
3) Checksum

DSC

Digital Selective Calling Information

Format unknown

DSE

Extended DSC

Format unknown

DSI

DSC Transponder Initiate

Format unknown

DSR

DSC Transponder Response

Format unknown

DTM Datum Reference

Format unknown

FSI

Frequency Set Information

       1      2      3 4 5
       |      |      | | |
$--FSI,xxxxxx,xxxxxx,c,x*hh

1) Transmitting Frequency
2) Receiving Frequency
3) Communications Mode (NMEA Syntax 2)
4) Power Level
5) Checksum

GBS

GPS Satellite Fault Detection

Format unknown

The NMEA 0183 Protocol

9

GGA Global Positioning System Fix Data. Time, Position and fix related data

for a GPS receiver

                                                     11
       1         2       3 4        5 6 7  8   9  10 |  12 13  14   15
       |         |       | |        | | |  |   |   | |   | |   |    |
$--GGA,hhmmss.ss,llll.ll,a,yyyyy.yy,a,x,xx,x.x,x.x,M,x.x,M,x.x,xxxx*hh

 1) Time (UTC)
 2) Latitude
 3) N or S (North or South)
 4) Longitude
 5) E or W (East or West)
 6) GPS Quality Indicator,
    0 - fix not available,
    1 - GPS fix,
    2 - Differential GPS fix
 7) Number of satellites in view, 00 - 12
 8) Horizontal Dilution of precision
 9) Antenna Altitude above/below mean-sea-level (geoid) 
10) Units of antenna altitude, meters
11) Geoidal separation, the difference between the WGS-84 earth
    ellipsoid and mean-sea-level (geoid), "-" means mean-sea-level below ellipsoid
12) Units of geoidal separation, meters
13) Age of differential GPS data, time in seconds since last SC104
    type 1 or 9 update, null field when DGPS is not used
14) Differential reference station ID, 0000-1023
15) Checksum

Geographic Position, Loran-C

GLC
                                          12    14
       1    2   3 4   5 6   7 8   9 10  11|   13|
       |    |   | |   | |   | |   | |   | |   | |
$--GLC,xxxx,x.x,a,x.x,a,x.x,a.x,x,a,x.x,a,x.x,a*hh

 1) GRI Microseconds/10
 2) Master TOA Microseconds
 3) Master TOA Signal Status
 4) Time Difference 1 Microseconds
 5) Time Difference 1 Signal Status
 6) Time Difference 2 Microseconds
 7) Time Difference 2 Signal Status
 8) Time Difference 3 Microseconds
 9) Time Difference 3 Signal Status
10) Time Difference 4 Microseconds
11) Time Difference 4 Signal Status
12) Time Difference 5 Microseconds
13) Time Difference 5 Signal Status
14) Checksum

GLL

Geographic Position – Latitude/Longitude

       1       2 3        4 5         6 7
       |       | |        | |         | |
$--GLL,llll.ll,a,yyyyy.yy,a,hhmmss.ss,A*hh

1) Latitude
2) N or S (North or South)
3) Longitude
4) E or W (East or West)
5) Time (UTC)
6) Status A - Data Valid, V - Data Invalid
7) Checksum

10

The NMEA 0183 Protocol

GRS

GPS Range Residuals

Format unknown

GST

GPS Pseudorange Noise Statistics

Format unknown

GSA GPS DOP and active satellites

       1 2 3                         14 15  16  17 18
       | | |                         |  |   |   |  |
$--GSA,a,a,x,x,x,x,x,x,x,x,x,x,x,x,x,x,x.x,x.x,x.x*hh

 1) Selection mode
 2) Mode
 3) ID of 1st satellite used for fix
 4) ID of 2nd satellite used for fix
 ...
14) ID of 12th satellite used for fix
15) PDOP in meters
16) HDOP in meters
17) VDOP in meters
18) Checksum

GSV

Satellites in view

       1 2 3 4 5 6 7     n
       | | | | | | |     |
$--GSV,x,x,x,x,x,x,x,...*hh

1) total number of messages
2) message number
3) satellites in view
4) satellite number
5) elevation in degrees
6) azimuth in degrees to true
7) SNR in dB
more satellite infos like 4)-7)
n) Checksum

GTD

Geographic Location in Time Differences

       1   2   3   4   5  6
       |   |   |   |   |  |
$--GTD,x.x,x.x,x.x,x.x,x.x*hh

1) time difference
2) time difference
3) time difference
4) time difference
5) time difference
n) Checksum

GXA

TRANSIT Position – Latitude/Longitude, Location and Time of TRANSIT Fix at Waypoint

obsolete

The NMEA 0183 Protocol

11

HDG Heading – Deviation & Variation

       1   2   3 4   5 6
       |   |   | |   | |
$--HDG,x.x,x.x,a,x.x,a*hh

1) Magnetic Sensor heading in degrees
2) Magnetic Deviation, degrees
3) Magnetic Deviation direction, E = Easterly, W = Westerly
4) Magnetic Variation degrees
5) Magnetic Variation direction, E = Easterly, W = Westerly
6) Checksum

HDM Heading – Magnetic

       1   2 3
       |   | |
$--HDM,x.x,M*hh

1) Heading Degrees, magnetic
2) M = magnetic
3) Checksum

HDT

Heading – True

       1   2 3
       |   | |
$--HDT,x.x,T*hh

1) Heading Degrees, true
2) T = True
3) Checksum

HSC

Heading Steering Command

       1   2 3   4  5
       |   | |   |  |
$--HSC,x.x,T,x.x,M,*hh

1) Heading Degrees, True
2) T = True
3) Heading Degrees, Magnetic
4) M = Magnetic
5) Checksum

The NMEA 0183 Protocol

LCD

Loran-C Signal Data

       1    2   3   4   5   6   7   8   9   10  11  12  13  14
       |    |   |   |   |   |   |   |   |   |   |   |   |   |
$--LCD,xxxx,xxx,xxx,xxx,xxx,xxx,xxx,xxx,xxx,xxx,xxx,xxx,xxx*hh

12

 1) GRI Microseconds/10
 2) Master Relative SNR
 3) Master Relative ECD
 4) Time Difference 1 Microseconds
 5) Time Difference 1 Signal Status
 6) Time Difference 2 Microseconds
 7) Time Difference 2 Signal Status
 8) Time Difference 3 Microseconds
 9) Time Difference 3 Signal Status
10) Time Difference 4 Microseconds
11) Time Difference 4 Signal Status
12) Time Difference 5 Microseconds
13) Time Difference 5 Signal Status
14) Checksum

MSK MSK Receiver Interface (for DGPS Beacon Receivers)

       1     2  3   4  5 6  
       |     |  |   |  | |
$GPMSK,xxx.x,xx,xxx,xx,N*hh

1) Frequency in kHz (283.5 to 325.0)
2) Frequency Selection
   M1 = Manual
   A1 = Automatic (field 1 empty)
3) MSK bit rate (100 or 200)
4) Bit Rate Selection
   M2 = Manual
   A2 = Automatic (field 3 empty)
5) Period of output of performance status message, 0 to 100 seconds ($CRMSS)
6) Checksum

MSS MSK Receiver Signal Status

Format unknown

MWD Wind Direction & Speed

Format unknown

MTW Water Temperature

       1   2 3
       |   | | 
$--MTW,x.x,C*hh

1) Degrees
2) Unit of Measurement, Celcius
3) Checksum

The NMEA 0183 Protocol

13

MWV Wind Speed and Angle

       1   2 3   4 5
       |   | |   | |
$--MWV,x.x,a,x.x,a*hh

1) Wind Angle, 0 to 360 degrees
2) Reference, R = Relative, T = True
3) Wind Speed
4) Wind Speed Units, K/M/N
5) Status, A = Data Valid
6) Checksum

OLN

Omega Lane Numbers

obsolete

OSD Own Ship Data

       1   2 3   4 5   6 7   8   9 10
       |   | |   | |   | |   |   | |
$--OSD,x.x,A,x.x,a,x.x,a,x.x,x.x,a*hh

 1) Heading, degrees true
 2) Status, A = Data Valid
 3) Vessel Course, degrees True
 4) Course Reference
 5) Vessel Speed
 6) Speed Reference
 7) Vessel Set, degrees True
 8) Vessel drift (speed)
 9) Speed Units
10) Checksum

ROO Waypoints in Active Route

       1                n
       |                | 
$--ROO,c---c,c---c,....*hh

1) waypoint ID
 ...
n) checksum

RMA Recommended Minimum Navigation Information

                                                   12
       1 2       3 4        5 6   7   8   9   10  11|
       | |       | |        | |   |   |   |   |   | |
$--RMA,A,llll.ll,a,yyyyy.yy,a,x.x,x.x,x.x,x.x,x.x,a*hh

 1) Blink Warning
 2) Latitude
 3) N or S
 4) Longitude
 5) E or W
 6) Time Difference A, µS
 7) Time Difference B, µS
 8) Speed Over Ground, Knots
 9) Track Made Good, degrees true
10) Magnetic Variation, degrees
11) E or W
12) Checksum

The NMEA 0183 Protocol

14

RMB Recommended Minimum Navigation Information
                                                            14
       1 2   3 4    5    6       7 8        9 10  11  12  13|
       | |   | |    |    |       | |        | |   |   |   | |
$--RMB,A,x.x,a,c--c,c--c,llll.ll,a,yyyyy.yy,a,x.x,x.x,x.x,A*hh

 1) Status, V = Navigation receiver warning
 2) Cross Track error - nautical miles
 3) Direction to Steer, Left or Right
 4) TO Waypoint ID
 5) FROM Waypoint ID
 6) Destination Waypoint Latitude
 7) N or S
 8) Destination Waypoint Longitude
 9) E or W
10) Range to destination in nautical miles
11) Bearing to destination in degrees True
12) Destination closing velocity in knots
13) Arrival Status, A = Arrival Circle Entered
14) Checksum

RMC Recommended Minimum Navigation Information
                                                           12
       1         2 3       4 5        6 7   8   9    10  11|
       |         | |       | |        | |   |   |    |   | |
$--RMC,hhmmss.ss,A,llll.ll,a,yyyyy.yy,a,x.x,x.x,xxxx,x.x,a*hh

 1) Time (UTC)
 2) Status, V = Navigation receiver warning
 3) Latitude
 4) N or S
 5) Longitude
 6) E or W
 7) Speed over ground, knots
 8) Track made good, degrees true
 9) Date, ddmmyy
10) Magnetic Variation, degrees
11) E or W
12) Checksum

ROT

Rate Of Turn

       1   2 3
        |   | |
$--ROT,x.x,A*hh

1) Rate Of Turn, degrees per minute, "-" means bow turns to port
2) Status, A means data is valid
3) Checksum

RPM Revolutions

       1 2 3   4   5 6
       | | |   |   | |
$--RPM,a,x,x.x,x.x,A*hh

1) Source; S = Shaft, E = Engine
2) Engine or shaft number
3) Speed, Revolutions per minute
4) Propeller pitch, % of maximum, "-" means astern
5) Status, A means data is valid
6) Checksum

15

The NMEA 0183 Protocol

RSA

Rudder Sensor Angle

       1   2 3   4 5
       |   | |   | |
$--RSA,x.x,A,x.x,A*hh

1) Starboard (or single) rudder sensor, "-" means Turn To Port
2) Status, A means data is valid
3) Port rudder sensor
4) Status, A means data is valid
5) Checksum

RADAR System Data

RSD
                                                       14
       1   2   3   4   5   6   7   8   9   10  11 12 13|
       |   |   |   |   |   |   |   |   |   |   |   | | |
$--RSD,x.x,x.x,x.x,x.x,x.x,x.x,x.x,x.x,x.x,x.x,x.x,a,a*hh

 9) Cursor Range From Own Ship
10) Cursor Bearing Degrees Clockwise From Zero
11) Range Scale
12) Range Units
14) Checksum

RTE

Routes

       1   2   3 4    5           x    n
       |   |   | |    |           |    |
$--RTE,x.x,x.x,a,c--c,c--c, ..... c--c*hh

1) Total number of messages being transmitted
2) Message Number
3) Message mode
   c = complete route, all waypoints
   w = working route, the waypoint you just left, the waypoint you're heading to,
       then all the rest
4) Waypoint ID
x) More Waypoints
n) Checksum

SFI

Scanning Frequency Information

       1   2   3      4                     n
       |   |   |      |                     |
$--SFI,x.x,x.x,xxxxxx,c .......... xxxxxx,c*hh

1) Total Number Of Messages
2) Message Number
3) Frequency 1
4) Mode 1
n) Checksum

STN

Multiple Data ID

       1   2
       |   |
$--STN,x.x,*hh

1) Talker ID Number
2) Checksum

The NMEA 0183 Protocol

16

TLL

Target Latitude and Longitude

Format unknown

TRF

TRANSIT Fix Data

obsolete

TTM Tracked Target Message

                                        11     13
       1  2   3   4 5   6   7 8   9   10|    12| 14
       |  |   |   | |   |   | |   |   | |    | | |
$--TTM,xx,x.x,x.x,a,x.x,x.x,a,x.x,x.x,a,c--c,a,a*hh

 1) Target Number
 2) Target Distance
 3) Bearing from own ship
 4) Bearing Units
 5) Target speed
 6) Target Course
 7) Course Units
 8) Distance of closest-point-of-approach
 9) Time until closest-point-of-approach "-" means increasing
10) "-" means increasing
11) Target name
12) Target Status
13) Reference Target
14) Checksum

VBW Dual Ground/Water Speed

       1   2   3 4   5   6 7
       |   |   | |   |   | |
$--VBW,x.x,x.x,A,x.x,x.x,A*hh

1) Longitudinal water speed, "-" means astern
2) Transverse water speed, "-" means port
3) Status, A = data valid
4) Longitudinal ground speed, "-" means astern
5) Transverse ground speed, "-" means port
6) Status, A = data valid
7) Checksum

VDR

Set and Drift

       1   2 3   4 5   6 7
       |   | |   | |   | |
$--VDR,x.x,T,x.x,M,x.x,N*hh

1) Degress True
2) T = True
3) Degrees Magnetic
4) M = Magnetic
5) Knots (speed of current)
6) N = Knots
7) Checksum

The NMEA 0183 Protocol

17

VHW Water Speed and Heading

       1   2 3   4 5   6 7   8 9
       |   | |   | |   | |   | |
$--VHW,x.x,T,x.x,M,x.x,N,x.x,K*hh

1) Degress True
2) T = True
3) Degrees Magnetic
4) M = Magnetic
5) Knots (speed of vessel relative to the water)
6) N = Knots
7) Kilometers (speed of vessel relative to the water)
8) K = Kilometres
9) Checksum

VLW Distance Traveled through Water

       1   2 3   4 5
       |   | |   | |
$--VLW,x.x,N,x.x,N*hh

1) Total cumulative distance
2) N = Nautical Miles
3) Distance since Reset
4) N = Nautical Miles
5) Checksum

VPW Speed – Measured Parallel to Wind

       1   2 3   4 5
       |   | |   | |
$--VPW,x.x,N,x.x,M*hh

1) Speed, "-" means downwind
2) N = Knots
3) Speed, "-" means downwind
4) M = Meters per second
5) Checksum

VTG

Track Made Good and Ground Speed

       1   2 3   4 5  6 7   8 9
       |   | |   | |  | |   | |
$--VTG,x.x,T,x.x,M,x.x,N,x.x,K*hh

1) Track Degrees
2) T = True
3) Track Degrees
4) M = Magnetic
5) Speed Knots
6) N = Knots
7) Speed Kilometers Per Hour
8) K = Kilometres Per Hour
9) Checksum

The NMEA 0183 Protocol

18

VWR Relative Wind Speed and Angle

        1  2  3  4  5  6  7  8 9
        |  |  |  |  |  |  |  | |
$--VWR,x.x,a,x.x,N,x.x,M,x.x,K*hh

1) Wind direction magnitude in degrees
2) Wind direction Left/Right of bow
3) Speed
4) N = Knots
5) Speed
6) M = Meters Per Second
7) Speed
8) K = Kilometers Per Hour
9) Checksum

WCV Waypoint Closure Velocity

       1   2 3    4
       |   | |    |
$--WCV,x.x,N,c--c*hh

1) Velocity
2) N = knots
3) Waypoint ID
4) Checksum

WDC Distance to Waypoint – Great Circle

Format unknown

WDR Distance to Waypoint – Rhumb Line

Format unknown

WNC Distance – Waypoint to Waypoint

       1   2 3   4 5    6    7
       |   | |   | |    |    |
$--WNC,x.x,N,x.x,K,c--c,c--c*hh

1) Distance, Nautical Miles
2) N = Nautical Miles
3) Distance, Kilometers
4) K = Kilometers
5) TO Waypoint
6) FROM Waypoint
7) Checksum

WPL Waypoint Location

       1       2 3        4 5    6
       |       | |        | |    |
$--WPL,llll.ll,a,yyyyy.yy,a,c--c*hh

1) Latitude
2) N or S (North or South)
3) Longitude
4) E or W (East or West)
5) Waypoint Name
6) Checksum

The NMEA 0183 Protocol

19

XDR

Cross Track Error – Dead Reckoning

       1 2   3 4            n
       | |   | |            |
$--XDR,a,x.x,a,c--c, ..... *hh

1) Transducer type
2) Measurement data
3) Units of measurement
4) Name of transducer
x) More of the same
n) Checksum

XTE

Cross-Track Error – Measured

       1 2 3   4 5  6
       | | |   | |  |
$--XTE,A,A,x.x,a,N,*hh

1) Status
   V = LORAN-C blink or SNR warning
   A = general warning flag or other navigation systems when a reliable
       fix is not available
2) Status
   V = Loran-C cycle lock warning flag
   A = OK or not used
3) Cross track error magnitude
4) Direction to steer, L or R
5) Cross track units. N = Nautical Miles
6) Checksum

XTR

Cross Track Error – Dead Reckoning

       1   2 3 4
       |   | | |
$--XTR,x.x,a,N*hh

1) Magnitude of cross track error
2) Direction to steer, L or R
3) Units, N = Nautical Miles
4) Checksum

ZDA

Time & Date – UTC, Day, Month, Year and Local Time Zone

       1         2  3  4    5  6  7
       |         |  |  |    |  |  |
$--ZDA,hhmmss.ss,xx,xx,xxxx,xx,xx*hh

1) Local zone minutes description, same sign as local hours
2) Local zone description, 00 to +/- 13 hours
3) Year
4) Month, 01 to 12
5) Day, 01 to 31
6) Time (UTC)
7) Checksum

ZDL

Time and Distance to Variable Point

Format unknown

The NMEA 0183 Protocol

20

ZFO

UTC & Time from Origin Waypoint

       1         2         3    4
       |         |         |    |
$--ZFO,hhmmss.ss,hhmmss.ss,c--c*hh

1) Time (UTC)
2) Elapsed Time
3) Origin Waypoint ID
4) Checksum

ZTG

UTC & Time to Destination Waypoint

       1         2         3    4
       |         |         |    |
$--ZTG,hhmmss.ss,hhmmss.ss,c--c*hh

1) Time (UTC)
2) Time Remaining
3) Destination Waypoint ID
4) Checksum

The NMEA 0183 Protocol

21

6. Some Proprietary Sentences

$PGRMC   Sensor Configuration Information

Garmin proprietary sentence

                                        13  14
       1 2   3  4   5   6   7   8   9 10| 12|
       | |   |  |   |   |   |   |   | | | | |
$PGRMC,A,x.x,hh,x.x,x.x,x.x,x.x,x.x,c,c,2,c*hh

 1) Fix mode, A=automatic (only option)
 2) Altitude above/below mean sea level, -1500.0 to 18000.0 meters 
 3) Earth datum index. If the user datum index (96) is specified,
    fields 5-8 must contain valid values. Otherwise, fields 4-8 must be null.
 4) User earth datum semi-major axis, 6360000.0 to 6380000.0 meters (.001 meters
    resolution) 
 5) User earth datum inverse flattening factor, 285.0 to 310.0 (10-9 resolution)
 6) User earth datum delta x earth centered coordinate, -5000.0 to 5000.0 meters
    (1 meter resolution)
 7) User earth datum delta y earth centered coordinate, -5000.0 to 5000.0 meters
    (1 meter resolution)
 8) User earth datum delta z earth centered coordinate, -5000.0 to 5000.0 meters
    (1 meter resolution)
 9) Differential mode, A = automatic (output DGPS data when available, non-DGPs
    otherwise), D = differential exclusively (output only differential fixes)
10) NMEA Baud rate, 1 = 1200, 2 = 2400, 3 = 4800, 4 = 9600
11) Filter mode, 2 = no filtering (only option)
12) PPS mode, 1 = No PPS, 2 = 1 Hz
13) Checksum

$PGRME   Estimated Position Error

Garmin proprietary sentence

       1   2 3   4 5   6 7
       |   | |   | |   | |
$PGRME,x.x,M,x.x,M,x.x,M*hh

1) Estimated horizontal position error (HPE)
2) Unit, metres
3) Estimated vertical error (VPE)
4) Unit, metres
5) Overall spherical equivalent position error
6) Unit, metres
7) Checksum

The NMEA 0183 Protocol

22

$PGRMF   Position Fix Sentence

Garmin proprietary sentence

                                                         10  12        15
       1   2   3      4      5   6         7 8          9 | 11|   13  14| 16
       |   |   |      |      |   |         | |          | | | |   |   | | |
$PGRMF,x.x,x.x,ddmmyy,hhmmss,x.x,ddmm.mmmm,c,dddmm.mmmm,c,c,c,x.x,x.x,c,c*hh

 1) GPS week number (0 - 1023)
 2) GPS seconds (0 - 604799) 
 3) UTC date of position fix
 4) UTC time of position fix
 5) GPS leap second count 
 6) Latitude
 7) N or S
 8) Longitude
 9) E or W 
10) Mode
    M = manual
    A = automatic
11) Fix type
    0 = no fix
    1 = 2D fix
    2 = 3D fix
12) Speed over ground, 0 to 999 kilometers/hour
13) Course over ground, 0 to 359 degrees, true
14) Position dilution of precision, 0 to 9 (rounded to nearest integer value)
15) Time dilution of precision, 0 to 9 (rounded to nearest integer value)
16) Checksum

$PGRMI   Sensor Initialisation Information

Garmin proprietary sentence

       1        2 3        4 5      6      7
       |        | |        | |      |      |
$PGRMI,ddmm.mmm,N,ddmm.mmm,E,ddmmyy,hhmmss*hh

1) Latitude
2) N or S
3) Longitude
4) E or W
5) Current UTC date
6) Current UTC time
7) Checksum

$PGRMM   Map Datum

Garmin proprietary sentence

       1     2
       |     |
$PGRMM,c---c*hh

1) Currently active horizontal datum (WGS-84, NAD27 Canada, ED50, a.s.o)
2) Checksum

The NMEA 0183 Protocol

23

$PGRMO   Output Sentence Enable/Disable

Garmin proprietary sentence

       1     2 3
       |     | |
$PGRMO,ccccc,c*hh

1) Target sentence description (e.g., PGRMT, GPGSV, etc.)
2) Target sentence mode
   0 = disable specified sentence
   1 = enable specified sentence
   2 = disable all
   3 = enable all output sentences (except GPALM)
3) Checksum

$PGRMT   Sensor Status Information

Garmin proprietary sentence

       1     2 3 4 5 6 7 8   9 10
       |     | | | | | | |   | |
$PGRMT,c...c,c,c,c,c,c,c,x.x,c*hh

 1) Product, model and software version 
    e.g. "GPS25VEE] 1.10"
 2) Rom checksum test
    P = pass
    F = fail 
 3) Receiver failure discrete
    P = pass
     F = fail
 4) Stored data lost
    R = retained
    L = lost
 5) Real time clock lost
    R = retained
    L = lost 
 6) 0scillator drift discrete
    P = pass
    F = excessive drift detected
 7) Data collection discrete
    C = collecting
    null if not collecting
 8) Board temperature in degrees C
 9) Board configuration data
    R = retained
    L = lost
10) Checksum

$PGRMV   3D Velocity

Garmin proprietary sentence

       1   2   3   4
       |   |   |   |
$PGRMV,x.x,x.x,x.x*hh

1) True east velocity, -999.9 to 9999.9 meters/second 
2) True north velocity, -999.9 to 9999.9 meters/second 
3) Up velocity, -999.9 to 9999.9 meters/second
4) Checksum

The NMEA 0183 Protocol

24

$PGRMZ   Altitude Information

Garmin proprietary sentence

       1   2 3 4
       |   | | |
$PGRMZ,x.x,f,h*hh

1) Altitude
2) Unit, feets
3) Position fix dimensions
   2 user altitude
   3 GPS altitude
4) Checksum

$PSLIB   Differental GPS Beacon Receiver Control

Starlink, Inc. proprietary sentence, used by Garmin and others

       1   2   3 4
       |   |   | |
$PSLIB,x.x,x.x,c*hh

1) Frequency
2) Bit rate
3) Request type
   J = status request
   K = configuration request
   blank = tuning message
4) Checksum

The NMEA 0183 Protocol

7. Manufacturer Codes

Note: This list is out-of-date, but perhaps still useful.

American Solar Power
Aetna Engineering

Airmar Technology
Antenna Specialists
Analytyx Electronic Systems
Anschutz of America
Apelco
American Pioneer, Inc.
Amperex, Inc.
Aqua-Chem, Inc.
Aquadynamics, Inc.

Asian American Resources
Auto-Comm Engineering Corporation
ACR Electronics, Inc.
Arco Solar, Inc.
Advanced Control Technology
Airguide Instrument Company
Autohelm of America
Aiphone Corporation
Alden Electronics, Inc.

AAR
ACE
ACR
ACS
ACT
AGI
AHA
AIP
ALD
AMR AMR Systems
AMT
ANS
ANX
ANZ
APC
APN
APX
AQC
AQD
AQM Aqua Meter Instrument Company
ASP
ATE
ATM Atlantic Marketing Company, Inc.
ATR
ATV
AVN
AWA
BBL
BBR
BDV
BEC
BGS
BGT
BHE
BHR
BLB
BME
BNI
BNS
BRM Mel Barr Company
BRY
BTH
BTK
BTS
BXA
CAT
CBN
CCA
CCC
CCL
CCM Coastal Communications
Cordic Company
CDC
Ceco Communications, Inc.
CEC
CHI
Charles Industries, Limited
CKM Cinkel Marine Electronics Industries
CMA
CMC Coe Manufacturing Company

Airtron
Activation, Inc.
Advanced Navigation, Inc.
Awa New Zealand, Limited
BBL Industries, Inc.
BBR and Associates
Brisson Development, Inc.
Boat Electric Company
Barringer Geoservice
Brookes and Gatehouse, Inc.
BH Electronics
Bahr Technologies, Inc.
Bay Laboratories
Bartel Marine Electronics
Neil Brown Instrument Systems
Bowditch Navigation Systems

Byrd Industries
Benthos, Inc.
Baltek Corporation
Boat Sentry, Inc.
Bendix-Avalex, Inc.
Catel
Cybernet Marine Products
Copal Corporation of America
Coastal Communications Company
Coastal Climate Company

Societe Nouvelle D'Equiment du Calvados

25

Crystek Crystals Corporation
Communication Systems International, Inc.

Cast, Inc.
Combined Services
Current Alternatives
Cetec Benmar
Cell-tech Communications
Castle Electronics
C-Tech, Limited
Continental Instruments

Cushman Electronics, Inc.
C-Map, s.r.l.
Coastal Marine Sales Company
CourseMaster USA, Inc.
Coastal Navigator
Cynex Manufactoring Company
Computrol, Inc.
Compunav
Columbus Positioning, Inc.
CPT, Inc.
Crystal Electronics, Limited

CME
CMP
CMS
CMV
CNV
CNX
CPL
CPN
CPS
CPT
CRE
CRO The Caro Group
CRY
CSI
CSM Comsat Maritime Services
CST
CSV
CTA
CTB
CTC
CTE
CTL
CNI
CWD Cubic Western Data
CWV Celwave R.F., Inc.
cYz, Inc.
CYZ
Dolphin Components Corporation
DCC
Debeg GmbH
DEB
Defender Industries, Inc.
DFI
Digicourse, Inc.
DGC
Digital Marine Electronics Corp.
DME
Datamarine International, Inc.
DMI
Dornier System GmbH
DNS
Del Norte Technology, Inc.
DNT
Danaplus, Inc.
DPS
R.L. Drake Company
DRL
Dynascan Corporation
DSC
Dynamote Corporation
DYN
Dytek Laboratories, Inc.
DYT
Emergency Beacon Corporation
EBC
Echotec, Inc.
ECT
EEV, Inc.
EEV
Efcom Communication Systems
EFC
Electronic Devices, Inc.
ELD
EMC Electric Motion Company
EMS
ENA
ENC
EPM Epsco Marine
Eastprint, Inc.
EPT
The Ericsson Corporation
ERC
European Space Agency
ESA
Fluiddyne
FDN
Fish Hawk Electronics
FHE
FJN
Jon Fluke Company
FMM First Mate Marine Autopilots
FNT
FRC
FTG
FUJ
FEC

Electro Marine Systems, Inc.
Energy Analysts, Inc.
Encron, Inc.

Franklin Net and Twine, Limited
The Fredericks Company
T.G. Faria Corporation
Fujitsu Ten Corporation of America
Furuno Electric Company (??)

The NMEA 0183 Protocol

26

Gro Electronics

Furuno USA, Inc.

Gulf Cellular Associates

Graphic Controls Corporation
Galax Integrated Systems
Global Positioning Instrument Corporation

Great Valley Technology
HAL Communications Corporation
Harris Corporation
Hy-Gain
Hi-Tec
Hewlett-Packard
Harco Manufacturing Company
Hart Systems, Inc.
Heart Interface, Inc.
Hull Electronics Company

FUR
GAM GRE America, Inc.
GCA
GES Geostar Corporation
GFC
GIS
GPI
GRM Garmin Corporation
GSC Gold Star Company, Limited
GTO
GVE Guest Corporation
GVT
HAL
HAR
HIG
HIT
HPK
HRC
HRT
HTI
HUL
HWM Honeywell Marine Systems
Icom of America, Inc.
ICO
International Fishing Devices
IFD
Instruments for Industry
IFI
Imperial Marine Equipment
IME
I.M.I.
IMI
ITT MacKay Marine
IMM
Impulse Manufacturing, Inc.
IMP
International Marketing and Trading, Inc.
IMT
Inmar Electronic and Sales, Inc.
INM
Intech, Inc.
INT
Intera Technologies, Ltd.
IRT
Innerspace Technology, Inc.
IST
Intermarine Electronics, Inc.
ITM
Itera, Limited
ITR
Jan Crystals
JAN
Ray Jefferson
JFR
Japan Marine Telecommunications
JMT
Japan Radio Company, Inc.
JRC
J-R Industries, Inc.
JRI
J-Tech Associates, Inc.
JTC
Jotron Radiosearch, Ltd.
JTR
KB Electronics, Ld.
KBE
KBM Kennebec Marine Company
KLA
Klein Associates, Inc.
KMR King Marine Radio Corporation
KNG King Radio Corporation
KOD
KRP
KVH
KYI
LAT
LEC
LMM Lamarche Manufacturing Company
LRD
LSE
LSP
LTF
LWR Lowrance Electronics Corportation
MCL Micrologic, Inc.

Koden Electronics Company, Ltd.
Krupp International, Inc.
KVH Company
Kyocera International, Inc.
Latitude Corporation
Lorain Electronics Corporation

Lorad
Littlemore Scientific Engineering
Laser Plot, Inc.
Littlefuse, Inc.

Micro-Now Instrument Company

Mieco, Inc.
Marconi International Marine Company
Martha Lake Electronics

MDL Medallion Instruments, Inc.
MEC Marine Engine Center, Inc.
MEG Maritec Engineering GmbH
MFR Modern Products, Ltd
MFW Frank W. Murphy Manufacturing
MGN Magellan Corporation
MGS MG Electronic Sales Corporation
MIE
MIM
MLE
MLN Matlin Company
Marlin Products
MLP
MLT
Miller Technologies
MMB Marsh-McBirney, Inc.
MME Marks Marine Engineering
MMP Metal Marine Pilot, Inc.
MMS Mars Marine Systems
MNI
MNT Marine Technology
MNX Marinex
MOT Motorola
MPN Memphis Net and Twine Company, Inc.
MQS Marquis Industries, Inc.
MRC Marinecomp, Inc.
MRE Morad Electronics Corporation
MRP Mooring Products of New England
MRR
II Morrow, Inc.
MRS Marine Radio Service
MSB Mitsubishi Electric Company, Ltd.
MSE Master Electronics
MSM Master Mariner, Inc.
MST Mesotech Systems, Ltd.
MTA Marine Technical Associates
MTG
MTK Martech, Inc.
MTR Mitre Corporation, Inc.
MTS Mets, Inc.
MUR Murata Erie North America
MVX Magnavox Advanced Products and

Narine Technical Assistance Group

Systems Company
Maxxima Marine

Navigation Sciences, Inc.

NovAtel Communications, Ltd.

Nautech, Limited
New England Fishing Gear, Inc.

MXX
MES Marine Electronics Service, Inc.
NAT
NEF
NMR Newmar
NGS
NOM Nav-Com, Inc.
NOV
NSM Northstar Marine
Novatech Designs, Ltd.
NTK
Navico
NVC
NVS
Navstar
NVO Navionics, s.p.a.
OAR O.A.R. Corporation
ODE
ODN Odin Electronics, Inc.
OIN
OKI
OLY
OMN Omnetics
ORE

Ocean Research

Ocean Data Equipment Corporation

Ocean instruments, Inc.
Oki Electronic Industry Company
Navstar Limited (Polytechnic Electronics)

The NMEA 0183 Protocol

27

SRS
TBB

Shipmate, Rauff & Sorensen, A/S
Thompson Brothers Boat Manufacturing
Company
Trade Commission of Norway (THE)
Tideland Signal
Thrane and Thrane A/A
Telesystems
Tamtech, Ltd.
Trimble Navigation
Tracor, Inc.
Techsonic Industries, Inc.
Talon Technology Corporation
Transtector Systems

TCN
TDL
THR
TLS
TMT
TNL
TRC
TSI
TTK
TTS
TWC Transworld Communications, Inc.
TXI
UME
UNI
UNP
UNF
VAN
VAR
VCM Videocom
VEX
VIS
VMR Vast Marketing Corporation
WAL Walport USA
WBG Westberg Manufacturing, Inc.
WEC Westinghouse Electric Corporation
WHA W-H Autopilots
WMM Wait Manufacturing and Marine Sales

Texas Instruments, Inc.
Umec
Uniden Corporation of America
Unipas, Inc.
Uniforce Electronics Company
Vanner, Inc.
Varian Eimac Associates

Vexillar
Vessel Information Systems, Inc.

Company

WMR Wesmar Electronics
WNG Winegard Company
WSE Wilson Electronics Corporation
WTC Watercom
WST West Electronics Ltd.
YAS

Yaesu Electronics

Ocean Technology
Pace

Ross Engineering Company
Rolfite Products, Inc.
RCS Global Communications, Inc.
Regency Electronics, Inc.

Petro-Com
P.T.I./Guest
Pathcom, Inc.
Racal Marine, Inc.
RCA Astro-Electronics
Raytheon Marine Company
RCA Service Company
Roach Engineering
Rochester Instruments, Inc.
Radar Devices

OTK
PCE
PDM Prodelco Marine Systems
Plath, C. Division of Litton
PLA
Pilot Instruments
PLI
Pernicka Marine Products
PMI
Pacific Marine Products
PMP
PRK
Perko, Inc.
PSM Pearce-Simpson
PTC
PTG
PTH
RAC
RAE
RAY
RCA
RCH
RCI
RDI
RDM Ray-Dar Manufacturing Company
REC
RFP
RGC
RGY
RMR RCA Missile and Surface Radar
RSL
Ross Laboratories, Inc.
RSM Robertson-Shipmate, USA
RWI
RME
RTN
SAI
SBR
SCR
SEA
SEC
SEP
SFN
SGC
SIG
SIM
SKA
SKP
SLI
SME
SMF
SML
SMI
SNV
SOM Sound Marine Electronics, Inc.
Sell Overseas America
SOV
Spelmar
SPL
Sound Powered Telephone
SPT
SRD Labs
SRD
Scientific Radio Systems, Inc.
SRS
Standard Radio and Telefon AB
SRT
Sea Scout Industries
SSI
Standard Communications
STC
STI
Sea-Temp Instrument Corporation
STM Si-Tex Marine Electronics
Savoy Electronics
SVY
Swoffer Marine Instruments, Inc.
SWI

Rockwell International
Racal Marine Electronics
Robertson Tritech Nyaskaien A/S
SAIT, Inc.
Sea-Bird electronics, Inc.
Signalcrafters, Inc.
SEA
Sercel Electronics of Canada
Steel and Engine Products, Inc.
Seafarer Navigation International
SGC, Inc.
Signet, Inc.
Simrad,Inc
Skantek Corporation
Skipper Electronics A/S
Starlink, Inc.
Shakespeare Marine Electronics
Seattle Marine and Fishing Supply Co.
Simerl Instruments
Sperry Marine, Inc.
Starnav Corporation

The NMEA 0183 Protocol

8. References

28

[1]

[2]

[3]

[4]

[5]

National Marine Electronics Association: http://www.nmea.org

Torsten Baumbach's web site: http://pandora.inf.uni-jena.de/ttbb/

Peter Bennett’s GPS and NMEA site: http://vancouver-webpages.com/pub/peter/index.html

Data Transmission Protocol Specification for Magellan Products. Revision 1.0. Magellan Corporation,
Santa Clara 1999. Available at: http://magellangps.com

This document describes the protocol used by Magellan’s consumer GPS units, including a number
of NMEA 0183 proprietary sentences.

SBA-1 Interfacing Manual. Revision 0.0. Communications Systems International, Inc, Calgary, 1999.
Available at: www.csi-dgps.com.

This manual explains the interfacing of the SBA-1 DGPS beacon receiver to numerous GPS units as
well as the CSI proprietary sentences used.
