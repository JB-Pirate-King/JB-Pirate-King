---
source_file: KMOU 세미나.pdf
last_synced: 2026-06-19 01:04
tags: [notion-sync, attachment]
---

# KMOU 세미나

2025 Maritime Cybersecurity & AI Seminar

2025.05.01.~2025.08.02.

Vulnerability Analysis of 
Maritime SW

[Team] SeaBugs

Speaker1. Taeyong Lee
Speaker2. Kyumin Lee

한국정보기술연구원

Whitehat
School

SeaBugs

Contents

Project Overview

01

Vulnerability Analysis 

02

Team Introduction

Analysis of OpenCPN

Project Background

Analysis of Orca G2

Attack Scenario

03

Conclusion

04

Attack Scenarios

Impact Analysis

Summary of Findings

Conclusions

Project Overview

01. Analysis of OpenCPN

02. Project Background

01. Team Introduction

Project Background

Team SeaBugs – Members

10 Members Total (8 WHS 3rd Trainees · Mentor · PL)

[PM] Taeyong Lee

Inseo You

Ju-chan Kim

Kyumin Lee

[Mentor] Minwoo Lee

Hyeonggwon Jin

Hyeonbin Yoon

Seunggwon Yang

JIHU PARK 

[PL] Yonghun No

Overall Project Involvement and Research

Advisory and Guidance

01. Team Introduction

Project Background

Increase in Maritime Cyber Incidents

On March 18, 2025, a cyber incident disrupted 
the communication networks of 116 oil tankers, 
severing onboard systems and ship-to-port 
connectivity.

Cyberattack attributed to Lab Dookhtegan

01. Team Introduction

Project Background

Increase in Maritime Cyber Incidents

Visited MADEX 2025

Observed real-world shipboard
OT systems

•

•

Collected information on next-
generation and autonomous vessels

•

KR (Korean Register) Senior Surveyor Meeting
•

Exchange of perspectives on maritime
security with a senior surveyor from
Korean Register
(First at WhiteHat School) Team Project 
Seminar

•

•

Visit to Busan Port VTS

Vessel Traffic Service System

VTS Roles & Position

Maritime Cybersecurity & VTS 
Importance

Southeast Information Security Cluster Visit

•

•

Region-Specific Security Cluster by 
KISA

Nation’s Only Ship System Testbed

01. Team Introduction

Project Background

Increase in Maritime Cyber Incidents

Bridge

AIS Reception

Hanara Training Vessel Tour (KMOU)

Hanara (Training Vessel)

GMDSS Training Lab Visit

Global Maritime Distress and Safety System

•
•

GMDSS training and education
Shipboard Analog Communications

OpenCPN

•
•

KMOU Convergence Security Lab

Project analysis site
Seven days of offline analysis and 
meetings

01. Team Introduction

Project Background

Increase in Maritime Cyber Incidents

Mandatory Maritime Cybersecurity for 
Newbuild Ships (from July 2024)

Growing Cyber Threats to 

Operational Vessels

Proactive Security Measures for Ship Software

On March 18, 2025, a cyber incident disrupted 
the communication networks of 116 oil tankers, 
severing onboard systems and ship-to-port 
connectivity.

Cyberattack attributed to Lab Dookhtegan

Vulnerability Analysis 

01. Analysis of OpenCPN

02. Analysis of Orca G2

02. Analysis of OpenCPN

Analysis of Orca G2

Vulnerability Summary

NO. Vulnerability Title

CVE ID

Category

Affected Program

Date Reported

1

2

3

4

5

6

7

8

Symbol Manipulate (Integrity Bypass)

-

Integrity Bypass

OpenCPN

File overwrite (Path traversal)

Code Injection (RCE)

CVE-2025-56810
(Reserved)

CVE-2025-56812
(Reserved)

Chart Downloader Plugin (RCE)

-

Command Injection (RCE)

heap Overflow (System Dos)

CVE-2025-56814
(Reserved)

CVE-2025-56813
(Reserved)

Path Traversal

OpenCPN

RCE

RCE

RCE

OpenCPN

OpenCPN

OpenCPN

Buffer Overflow

OpenCPN

25.07.01

25.07.10

25.07.10

25.07.15

25.07.25

25.07.24

TOCTOU (LPE)

CVE-2025-61037

Race Condition, LPE

SevenCs Orca G2

25.07.16

Unrestricted \\.\C: Access (System Dos, LPE)

CVE-2025-64699

System DoS, LPE

SevenCs Orca G2

25.07.26

02. Analysis of OpenCPN

Analysis of Orca G2

OpenCPN – Command Injection (CVE-2025-56814 Reserved)

Clicking a button-based 
command executes attacker-
supplied commands

Launcher Plugin

Execution of user-defined commands 
in the shell without filtering

▲

the calculator executed via attacker-defined commands

02. Analysis of OpenCPN

Analysis of Orca G2

OpenCPN – File overwrite (CVE-2025-56810 Reserved)

import_plugin()

No validation of metadata paths

Creation of metadata files without file path 
validation enables arbitrary file writes

▲

metadata test file

Metadata files created in a 
predefined home directory

02. Analysis of OpenCPN

Analysis of Orca G2

OpenCPN – Code Injection (CVE-2025-56812 Reserved)

Test Plugin

Blacklist-based validation

During the library loading process, only
blacklist-based validation is applied

What if a plugin containing a bind shell is
loaded?


RCE is possible

▲

a reverse shell after executing the test plugin

02. Analysis of OpenCPN

Analysis of Orca G2

OpenCPN – Symbol Manipulate (Integrity Bypass)

Confirmed disappearance of underwater obstacle 
symbols on the electronic chart


Such vulnerabilities pose severe risks during 

real-world navigation

chartsymbols.xml

When loading XML files defining chart symbols, only 

the filename is checked without further validation

02. Analysis of OpenCPN

Analysis of Orca G2

OpenCPN - Chart Downloader Plugin (RCE)

Chart Downloader

Downloads a ZIP file from <zipfile_location> and 
extracts it to a user-specified directory

Directory traversal is possible using ../ sequences

What if a malicious .bat file is extracted to the 
startup directory using ../ in the file path?

→ Automatically executed on reboot, enabling 
Remote Code Execution (RCE)

02. Analysis of OpenCPN

Analysis of Orca G2

OpenCPN – heap Overflow (CVE-2025-56813 Reserved)

A crash was observed using 
AddressSanitizer

edit_date

null byte heap overflow 
(null byte overwrite)

02. Analysis of OpenCPN

Analysis of Orca G2

ORCA G2 – TOCTOU (CVE-2025-61037)

Normal message

Program displays 
successful 
authentication

BUT

Checks directory existence 

using GetFileAttributesA, 

then calls CreateDirectoryA

and CopyFileA without 

additional validation

TOCTOU
(Time-of-Check to Time-of-Use) 
vulnerability occurs

※

NTFS feature that allows path redirection using junction directory links

If an attacker deletes the original path and inserts a 
junction directory between the check and use 
phases,
files can be created or copied into administrator-
only directories even with standard user privileges

▲ Insertion of a junction directory using HackTheFile

02. Analysis of OpenCPN

Analysis of Orca G2

ORCA G2 – Unrestricted \\.\C: Access (CVE-2025-64699)

Everyone RW

Standard users gain read and 
write access to the \\.\C: device

SetFileSecurityA

If the DACL is missing, the 
security descriptor for \\.\C: is 
configured to allow full access 
(Everyone RW)

※

•

•

•

•

DACL (Discretionary Access Control List)

Attacker

Boot failure caused by a VBR 
attack
Local privilege escalation via 
SAM hive dump and hash 
extraction
Disk metadata and NTFS 
corruption
Bypassing operating system 
security boundaries and 
evading forensic analysis

Attack Scenario

01. Attack Scenarios

02. Impact Analysis

03. Attack Scenarios

Impact Analysis

Maritime Cyber Attack Scenario Leveraging ORCA G2 Vulnerabilities

Internet Network

Onboard Isolated Network (Closed Network)

Onboard internet-
connected PC

Navigator

4

Injection and 
execution of 
malware in the 
closed network 
using storage 
devices

5

Navigation 
Support System 
Disruption

Satellite

Shipping 
Company

1

Information Gathering 
for Spear Phishing

Crew satellite email service

3

Spear-Phishing 
Email Delivery

2

Creation of Trojan malware 
disguised as a new electronic 
navigational chart (S-100 format)

Attacker

03. Attack Scenarios

Impact Analysis

Maritime Cyber Attack Scenario Leveraging ORCA G2 Vulnerabilities

Effects of ECDIS Failure

1. Immediate Safety Threats



Loss of position, obstacle, and          

depth information

2. Regulatory Violations



SOLAS V/19 non-compliance

3. Bridge Emergency Response

Reliance on paper charts and 

4. Operational & Economic Losses

Severe financial damage and 

radar





casualties

Physical and Economic Damage via 
Induced Vessel Collision

Conclusion

01. Summary of Findings

02. Conclusions

04. Key Findings

Lessons & Future Work

What We Found ?

A project analyzing recurring maritime cyber vulnerabilitiesand their real-world impact on navigation safety

a

Vulnerabilities >

Identified multiple exploitable 
vulnerabilities in maritime software

Security Impacts >

Confirmed security impacts on 
ECDIS and navigation systems

a

Attack Scenarios >

a

+

Serious Risk >

a

a

Demonstrated realistic attack scenarios 
using ORCA G2 and OpenCPN

Showed that common security flaws 
can lead to serious maritime risks

04. Key Findings

Lessons & Future Work

A Known Problem, Repeated Again

Microsoft Lean

Most identified vulnerabilities are long-known security issues

Examples include NULL DACL and TOCTOU, already well documented

04. Summary of Findings

Conclusions

The Problem Is Not New Attacks, but Insecure Design

Implementation

Design

The core problem 
lies in insecure 
implementation, 
not novel attacks

Security must be 
considered from 
the shipboard 
software design 
phase

Collaboration

Stronger 
collaboration 
among vendors, 
operators, and 
researchers is 
required

2025 Maritime Cybersecurity & AI Seminar

Thank you 
for your attention.

한국정보기술연구원

Whitehat
School

SeaBugs
