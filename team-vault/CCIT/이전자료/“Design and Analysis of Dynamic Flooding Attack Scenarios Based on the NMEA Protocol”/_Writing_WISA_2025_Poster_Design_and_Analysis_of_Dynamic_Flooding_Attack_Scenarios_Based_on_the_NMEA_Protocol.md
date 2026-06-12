---
source_file: _Writing_WISA_2025_Poster_Design_and_Analysis_of_Dynamic_Flooding_Attack_Scenarios_Based_on_the_NMEA_Protocol.pdf
last_synced: 2026-06-12 18:05
tags: [notion-sync, attachment]
---

# _Writing_WISA_2025_Poster_Design_and_Analysis_of_Dynamic_Flooding_Attack_Scenarios_Based_on_the_NMEA_Protocol

NMEA Under Siege: Dynamic Flooding Attacks
and Defenses

Taeyong Lee1, Inseo You1, Ju-chan Kim2, Kyumin Lee3, Hyeonggwon Jin4,
Seunggwon Yang5, Hyeonbin Yoon6, Yonghun No7, and Minwoo Lee8⋆

1 Soonchunhyang University (undergraduate student)
2 Dongguk University (undergraduate student)
3 Inha Technical College (undergraduate student)
4 Sejong University (undergraduate student)
5 Joongbu University (undergraduate student)
6 Korea University (undergraduate student)
7 CYTUR (Whitehat School PL)
8 Korea Maritime & Ocean University (Whitehat School Mentor)

Abstract. The NMEA protocol lacks authentication, encryption, and
integrity checks, making it vulnerable to spoofing and flooding. While
prior studies have focused on structural flaws, dynamic attack scenarios
remain underexplored. This paper presents a flooding attack simulation
using OpenCPN, where forged AIS messages generate a swarm of ghost
ships to overwhelm the chart display and obscure valid information. The
results demonstrate how such attacks can disrupt situational awareness.
To mitigate this risk, we propose lightweight countermeasures including
whitelist filtering, visual suppression, and anomaly detection.

Keywords: NMEA · Dynamic Flooding Attack · Visual Filtering.

1

Introduction

The National Marine Electronics Association (NMEA) protocol is widely used
for communication among maritime devices such as Global Positioning System
(GPS), radar, and autopilot systems. Designed for simplicity and interoperabil-
ity, it lacks essential security features like authentication, encryption, and in-
tegrity verification [1].

In contrast, the Controller Area Network (CAN)-based J1939 protocol in ve-
hicles has seen extensive security research, while NMEA remains underexplored.
Its low bandwidth and minimal structure limit the effectiveness of intrusion de-
tection and response systems [2].

Although some ships employ Internet Protocol (IP)-based filtering in line
with International Maritime Organization (IMO) guidelines, such methods are
vulnerable to spoofing and ineffective against high-volume flooding. The diversity

⋆ Corresponding author.

2

T. Lee et al.

of proprietary command sets across manufacturers further hinders consistent
security policy enforcement.

To address these limitations, we demonstrate a dynamic flooding attack using
real-world navigation software. Forged Automatic Identification System (AIS)
messages are used to saturate displays with false targets, masking real alerts and
degrading situational awareness. Our findings highlight the need for lightweight,
protocol-aware defenses for legacy maritime systems.

2 EXPERIMENT

2.1 Research Methodology

Fig. 1. Research Methodology

We first identified the absence of authentication, encryption, and integrity
checks in NMEA 0183/2000. To assess the impact of this gap, we configured
a virtual environment using OpenCPN, a widely used open-source navigation
software. A Python script was developed to inject forged AIS messages (AIVDM
format) at approximately 100 Hz, simulating a coordinated flooding attack.

By comparing the system’s behavior before and after injection, we observed
how message flow was disrupted, alerts were hidden, and information overload oc-
curred. Based on these observations, we suggest a multi-layered defense strategy
incorporating message whitelisting, anomaly-based filtering, and visual mitiga-
tion techniques.

2.2 Existing Research Analysis and Scenario Design

Although prior studies have examined static protocol vulnerabilities, dynamic
attack scenarios remain underexplored. Our approach manipulates NMEA AIS
Type 1 messages to simulate a swarm of non-existent vessels displayed in a
specific pattern within OpenCPN.

Dynamic Flooding Attack Scenario: Ghost Ship Swarm The attack script gen-
erates thousands of fake AIVDM messages using sequential Maritime Mobile
Service Identity (MMSI) values and calculated coordinates shaped into readable
characters. These messages are transmitted via a virtual serial port to flood
OpenCPN’s map display with “ghost ships,” forming patterns such as “WHS3”
around predefined base coordinates.

NMEA Under Siege: Dynamic Flooding Attacks and Defenses

3

Attack Objectives The goal is to impair situational awareness by visually over-
whelming Electronic Chart Display and Information Systems (ECDIS) or radar
displays, masking real-time alerts and making genuine vessels indistinguishable.

2.3 Experimental Environment Setup

We built the testbed on a standard Windows PC using OpenCPN with vir-
tual serial ports created by com0com. The Python script sends forged messages
through COM8, which is virtually linked to COM9, the input port for OpenCPN.
Table 1 summarizes the configuration.

Table 1. Experimental Environment Configuration

Hardware
Software
Serial Port Setup COM8 (script output) ↔ COM9 (OpenCPN input)

Standard PC (Windows OS)
OpenCPN, com0com, Python 3.x, PySerial

2.4 Experiment Execution and Observations

We conducted the experiment using OpenCPN, an open-source maritime nav-
igation software commonly used for both simulation and voyage planning. Its
support for external sensor inputs and AIS plugins makes it suitable for evalu-
ating AIS-based attack scenarios.

In our setup, OpenCPN was configured to receive synthetic AIS data via a
virtual serial port. Upon executing the Python script, a high volume of forged
AIVDM messages was transmitted, generating a swarm of ghost ships that
formed the characters “WHS3” around the specified coordinates (BASE_LAT,
BASE_LON).

Fig. 2. Attack Result: Ghost Ship Swarm in OpenCPN

Approximately 10,000 ghost vessels appeared across the chart, obscuring real
ship positions and alerts. This result confirms that the absence of authentication

4

T. Lee et al.

in the NMEA protocol allows not only data injection, but also visual distortion
of navigational displays—compromising situational awareness and increasing the
risk of operator error.

3 Proposed Countermeasures

To defend against dynamic flooding attacks in NMEA-based systems, we propose
three lightweight measures. First, a whitelist can restrict AIS messages to trusted
MMSI identifiers, blocking traffic from unknown or spoofed sources. Second,
visual filtering can reduce display clutter by fading or hiding low-confidence
targets, helping users focus on verified alerts. Third, simple anomaly detection
based on message frequency or vessel density can identify abnormal patterns
and trigger warnings. These methods can be integrated into existing navigation
software without major changes, offering practical protection for legacy maritime
systems.

4 Results and Discussion

The experiment demonstrated that a dynamic flooding attack using forged AIS
messages can severely degrade NMEA-based navigation systems. Approximately
10,000 ghost ships were injected, visually overwhelming the chart display and
masking genuine vessels and alerts.

This confirms a critical vulnerability: without authentication, NMEA traffic
can be easily manipulated, leading to operator misjudgment and loss of situa-
tional awareness. Although effective in simulation, the findings are limited to a
PC-based testbed and exclude other attack types such as spoofing or replay.

Future work should validate these scenarios in real vessel environments and

develop lightweight, automated defenses suitable for legacy systems.

Acknowledgement

This research was supported by the Ministry of SMEs and Startups(MSS), Korea
Institute for Advancement of Technology(KIAT) through the Innovation Devel-
opment(R&D) for Global Regulation-Free Special Zone

References

1. Ryu, D.-H., et al.: NMEA2000 and information security for ship networks. J. Ko-

rean Inst. Inf. Secur. Cryptol. 24(2) (2014)

2. Quigley, C., et al.: NMEA 2000 vulnerability to cyberattacks and mitigation.

NMEA Report (2024)

3. Popic, S., et al.: Enhancing cybersecurity of J1939 and NMEA 2000 via

presentation-layer encryption. Proc. IEEE ZINC (2025)

4. Murvay, P.-S., et al.: Security shortcomings and countermeasures for SAE J1939.

IEEE Trans. Veh. Technol. 67(5), 4325–4336 (2018)
