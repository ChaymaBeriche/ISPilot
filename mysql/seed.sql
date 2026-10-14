-- ============================================================
-- seed.sql — Complete ISP Lab Dataset
-- ALL DATA BELOW IS SYNTHETIC. Names, phone numbers, addresses and credentials are
-- invented demo values and do not belong to real people or systems.
-- Covers all 20 RAG scenarios + fixes from analysis
-- ============================================================

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
SET time_zone = "+00:00";
SET NAMES utf8mb4;

-- ============================================================
-- Re-runnable on purpose: every timestamp below is NOW()-relative
-- (DATE_SUB/DATE_ADD), computed fresh at whatever moment this file
-- actually executes — not a fixed calendar date. But
-- docker-entrypoint-initdb.d only runs this once, against an empty
-- data directory, so those relative offsets otherwise "freeze" at
-- container-init time and go stale as real time passes (this is what
-- caused get_auth_failures/get_session_history's rolling 24h-window
-- checks to stop matching seeded data after enough time passed).
--
-- Truncating first makes re-running this file safe (the INSERTs below
-- use fixed explicit IDs, so re-running without this would just hit
-- duplicate-key errors) — see run_tests.py, which re-runs this file
-- before every test suite run specifically to refresh these offsets.
-- FK checks disabled around this block only to avoid ordering this by
-- hand (users.username -> radcheck.username, ON DELETE CASCADE).
-- ============================================================

SET FOREIGN_KEY_CHECKS = 0;
TRUNCATE TABLE `nas`;
TRUNCATE TABLE `radgroupcheck`;
TRUNCATE TABLE `radgroupreply`;
TRUNCATE TABLE `radcheck`;
TRUNCATE TABLE `radusergroup`;
TRUNCATE TABLE `radreply`;
TRUNCATE TABLE `radpostauth`;
TRUNCATE TABLE `radacct`;
TRUNCATE TABLE `users`;
TRUNCATE TABLE `network_metrics`;
TRUNCATE TABLE `outages`;
TRUNCATE TABLE `tickets`;
SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================
-- NAS (Network Access Servers)
-- ============================================================

INSERT INTO `nas` (`id`, `nasname`, `shortname`, `type`, `ports`, `secret`, `server`, `community`, `description`) VALUES
(1, '172.19.0.0/16', 'docker-lab',   'other', NULL, 'CHANGE_ME_demo', NULL, NULL, 'Docker lab network'),
(2, '127.0.0.1',     'localhost',    'other', NULL, 'CHANGE_ME_demo', NULL, NULL, 'Localhost testing'),
(3, '10.0.0.1',      'isp-nas-main', 'other', NULL, 'CHANGE_ME_demo', NULL, NULL, 'Main ISP NAS');

-- ============================================================
-- RADGROUPCHECK — Authentication rules per group
-- ============================================================

INSERT INTO `radgroupcheck` (`id`, `groupname`, `attribute`, `op`, `value`) VALUES
(1, 'basic_users',   'Auth-Type', ':=', 'Accept'),
(2, 'premium_users', 'Auth-Type', ':=', 'Accept'),
(3, 'routers',       'Auth-Type', ':=', 'Accept'),
(4, 'blocked',       'Auth-Type', ':=', 'Reject');

-- ============================================================
-- RADGROUPREPLY — Plan attributes per group
-- basic_users:   8 Mbps down / 2 Mbps up / 1h session quota
-- premium_users: 50 Mbps down / 10 Mbps up / 24h session
-- routers:       no bandwidth limit / 24h session
-- ============================================================

INSERT INTO `radgroupreply` (`id`, `groupname`, `attribute`, `op`, `value`) VALUES
-- basic_users plan
(1,  'basic_users', 'Session-Timeout',          '=', '3600'),
(2,  'basic_users', 'WISPr-Bandwidth-Max-Down', '=', '8000000'),
(3,  'basic_users', 'WISPr-Bandwidth-Max-Up',   '=', '2000000'),
(4,  'basic_users', 'Framed-IP-Netmask',         '=', '255.255.255.0'),
-- premium_users plan
(5,  'premium_users', 'Session-Timeout',          '=', '86400'),
(6,  'premium_users', 'WISPr-Bandwidth-Max-Down', '=', '50000000'),
(7,  'premium_users', 'WISPr-Bandwidth-Max-Up',   '=', '10000000'),
(8,  'premium_users', 'Framed-IP-Netmask',         '=', '255.255.255.0'),
-- routers group (device auth, no bandwidth limit)
(9,  'routers', 'Session-Timeout',  '=', '86400'),
(10, 'routers', 'Framed-IP-Netmask', '=', '255.255.255.0');

-- ============================================================
-- RADCHECK — User credentials
-- Original devices (SIM-000001 to SIM-000010)
-- Scenario customers (karim.mansouri etc.)
-- New scenario customers (SIM-000011 to SIM-000019)
-- ============================================================

INSERT INTO `radcheck` (`id`, `username`, `attribute`, `op`, `value`) VALUES
-- Original simulator devices
(1,  'SIM-000001', 'Cleartext-Password', ':=', 'devicepass'),
(2,  'SIM-000002', 'Cleartext-Password', ':=', 'devicepass'),
(3,  'SIM-000003', 'Cleartext-Password', ':=', 'devicepass'),
(4,  'SIM-000004', 'Cleartext-Password', ':=', 'devicepass'),
-- S01: Karim — PPPoE auth failure (wrong password on router)
(5,  'karim.mansouri',   'Cleartext-Password', ':=', 'KarimPass2024!'),
-- S02: Leila — degraded line / low SNR
(6,  'leila.hamdi',      'Cleartext-Password', ':=', 'LeilaPass2024!'),
-- S03: Rami — zombie session
(7,  'rami.bouazizi',    'Cleartext-Password', ':=', 'RamiPass2024!'),
-- S04: Nour — quota exhausted
(8,  'nour.sfaxi',       'Cleartext-Password', ':=', 'NourPass2024!'),
-- S05: Tarek — outdated firmware
(9,  'tarek.belhaj',     'Cleartext-Password', ':=', 'TarekPass2024!'),
-- S06: Yasmine — MTU mismatch
(10, 'yasmine.karray',   'Cleartext-Password', ':=', 'YasminePass2024!'),
-- S07: Amine — forgot password / credential reset
(11, 'amine.gharbi',     'Cleartext-Password', ':=', 'AmineOldPass!'),
-- S08: Sarra — Lost-Carrier instability (marginal SNR 8 dB)
(12, 'sarra.mejri',      'Cleartext-Password', ':=', 'SarraPass2024!'),
-- S09: Mehdi — intermittent disconnections (SNR 9 dB, packet loss)
(13, 'mehdi.triki',      'Cleartext-Password', ':=', 'MehdiPass2024!'),
-- S10: Ines — slow speed (speed below plan)
(14, 'ines.chaabane',    'Cleartext-Password', ':=', 'InesPass2024!'),
-- S11: Omar — suspended account
(15, 'omar.hadj',        'Cleartext-Password', ':=', 'OmarPass2024!'),
-- S12: Rim — high latency / packet loss
(16, 'rim.belhadj',      'Cleartext-Password', ':=', 'RimPass2024!'),
-- S13: Bilel — plan upgrade request (hits quota daily)
(17, 'bilel.amor',       'Cleartext-Password', ':=', 'BilelPass2024!'),
-- S14: Dorra — WiFi confusion (line is fine, WiFi is issue)
(18, 'dorra.miled',      'Cleartext-Password', ':=', 'DorraPass2024!'),
-- S15: Sofien — factory reset scenario (all fixes failed)
(19, 'sofien.nasr',      'Cleartext-Password', ':=', 'SofienPass2024!');

-- ============================================================
-- RADUSERGROUP — User to group assignments
-- ============================================================

INSERT INTO `radusergroup` (`id`, `username`, `groupname`, `priority`) VALUES
-- Original devices
(1,  'SIM-000001',     'routers',       1),
(2,  'SIM-000002',     'routers',       1),
(3,  'SIM-000003',     'routers',       1),
(4,  'SIM-000004',     'routers',       1),
-- Scenario customers
(5,  'karim.mansouri', 'basic_users',   1),
(6,  'leila.hamdi',    'basic_users',   1),
(7,  'rami.bouazizi',  'premium_users', 1),
(8,  'nour.sfaxi',     'basic_users',   1),
(9,  'tarek.belhaj',   'premium_users', 1),
(10, 'yasmine.karray', 'premium_users', 1),
-- New scenario customers
(11, 'amine.gharbi',   'basic_users',   1),
(12, 'sarra.mejri',    'basic_users',   1),
(13, 'mehdi.triki',    'basic_users',   1),
(14, 'ines.chaabane',  'premium_users', 1),
(15, 'omar.hadj',      'basic_users',   1),
(16, 'rim.belhadj',    'premium_users', 1),
(17, 'bilel.amor',     'basic_users',   1),
(18, 'dorra.miled',    'premium_users', 1),
(19, 'sofien.nasr',    'premium_users', 1);

-- ============================================================
-- RADREPLY — Per-user reply attributes
-- nour.sfaxi and bilel.amor have Session-Timeout (quota limit)
-- ============================================================

INSERT INTO `radreply` (`id`, `username`, `attribute`, `op`, `value`) VALUES
-- Nour: basic plan with daily quota (Session-Timeout override)
(1, 'nour.sfaxi',  'Session-Timeout', ':=', '3600'),
-- Bilel: hits quota daily — same Session-Timeout override
(2, 'bilel.amor',  'Session-Timeout', ':=', '3600');

-- ============================================================
-- RADPOSTAUTH — Authentication history
-- Shows which users have recent failures vs successes
-- ============================================================

INSERT INTO `radpostauth` (`id`, `username`, `pass`, `reply`, `authdate`) VALUES
-- S01: Karim — 12 recent Access-Reject (PPPoE credential mismatch)
(1,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 65 MINUTE)),
(2,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 60 MINUTE)),
(3,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 55 MINUTE)),
(4,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 50 MINUTE)),
(5,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 45 MINUTE)),
(6,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 40 MINUTE)),
(7,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 35 MINUTE)),
(8,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 30 MINUTE)),
(9,  'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 25 MINUTE)),
(10, 'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 20 MINUTE)),
(11, 'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 15 MINUTE)),
(12, 'karim.mansouri', '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 10 MINUTE)),
-- S02: Leila — connected successfully (SNR issue not auth)
(13, 'leila.hamdi',    '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 110 MINUTE)),
(14, 'leila.hamdi',    '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 230 MINUTE)),
-- S03: Rami — last auth was Accept (zombie session, not auth failure)
(15, 'rami.bouazizi',  '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 180 MINUTE)),
-- S04: Nour — last auth Accept before quota hit
(16, 'nour.sfaxi',     '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 70 MINUTE)),
-- S05: Tarek — Accept (firmware issue not auth)
(17, 'tarek.belhaj',   '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 30 MINUTE)),
-- S06: Yasmine — Accept (MTU issue not auth)
(18, 'yasmine.karray', '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 45 MINUTE)),
-- S07: Amine — a few recent failures (trying old password)
(19, 'amine.gharbi',   '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 20 MINUTE)),
(20, 'amine.gharbi',   '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 15 MINUTE)),
(21, 'amine.gharbi',   '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 10 MINUTE)),
-- S08: Sarra — Accept but Lost-Carrier in session history
(22, 'sarra.mejri',    '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 90 MINUTE)),
-- S09: Mehdi — Accept (intermittent, not auth)
(23, 'mehdi.triki',    '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 60 MINUTE)),
-- S10: Ines — Accept (speed issue not auth)
(24, 'ines.chaabane',  '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 120 MINUTE)),
-- S11: Omar — rejected (account suspended at RADIUS level)
(25, 'omar.hadj',      '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 30 MINUTE)),
(26, 'omar.hadj',      '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 20 MINUTE)),
-- S12: Rim — Accept (latency issue not auth)
(27, 'rim.belhadj',    '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 45 MINUTE)),
-- S13: Bilel — Accept then Session-Timeout
(28, 'bilel.amor',     '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 80 MINUTE)),
-- S14: Dorra — Accept (WiFi issue not auth)
(29, 'dorra.miled',    '', 'Access-Accept', DATE_SUB(NOW(), INTERVAL 60 MINUTE)),
-- S15: Sofien — multiple failures after reboot attempts
(30, 'sofien.nasr',    '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 40 MINUTE)),
(31, 'sofien.nasr',    '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 30 MINUTE)),
(32, 'sofien.nasr',    '', 'Access-Reject', DATE_SUB(NOW(), INTERVAL 20 MINUTE));

-- ============================================================
-- RADACCT — Session history
-- Designed to trigger correct scenario diagnosis
-- ============================================================

INSERT INTO `radacct` (`radacctid`, `acctsessionid`, `acctuniqueid`, `username`, `nasipaddress`, `nasportid`, `acctstarttime`, `acctstoptime`, `acctsessiontime`, `acctinputoctets`, `acctoutputoctets`, `calledstationid`, `callingstationid`, `acctterminatecause`, `framedipaddress`) VALUES

-- S01: Karim — only Access-Reject sessions, never connects
(1,  'sess-k-001', 'uniq-k-001', 'karim.mansouri', '10.0.0.1', '1', DATE_SUB(NOW(), INTERVAL 65 MINUTE), NULL, NULL, 0, 0, 'ISP-NAS', 'SIM-000005', 'Access-Reject', ''),
(2,  'sess-k-002', 'uniq-k-002', 'karim.mansouri', '10.0.0.1', '1', DATE_SUB(NOW(), INTERVAL 30 MINUTE), NULL, NULL, 0, 0, 'ISP-NAS', 'SIM-000005', 'Access-Reject', ''),

-- S02: Leila — connected but NAS-Error disconnections (SNR issue)
(3,  'sess-l-001', 'uniq-l-001', 'leila.hamdi', '10.0.0.1', '2', DATE_SUB(NOW(), INTERVAL 110 MINUTE), NULL, 0, 1024000, 512000, 'ISP-NAS', 'SIM-000006', 'En cours', '100.64.6.3'),
(4,  'sess-l-002', 'uniq-l-002', 'leila.hamdi', '10.0.0.1', '2', DATE_SUB(NOW(), INTERVAL 230 MINUTE), DATE_SUB(NOW(), INTERVAL 110 MINUTE), 7200, 2048000, 1024000, 'ISP-NAS', 'SIM-000006', 'NAS-Error', '100.64.6.3'),
(5,  'sess-l-003', 'uniq-l-003', 'leila.hamdi', '10.0.0.1', '2', DATE_SUB(NOW(), INTERVAL 350 MINUTE), DATE_SUB(NOW(), INTERVAL 230 MINUTE), 7200, 2048000, 1024000, 'ISP-NAS', 'SIM-000006', 'NAS-Error', '100.64.6.3'),
(6,  'sess-l-004', 'uniq-l-004', 'leila.hamdi', '10.0.0.1', '2', DATE_SUB(NOW(), INTERVAL 470 MINUTE), DATE_SUB(NOW(), INTERVAL 350 MINUTE), 7200, 2048000, 1024000, 'ISP-NAS', 'SIM-000006', 'NAS-Error', '100.64.6.3'),

-- S03: Rami — zombie session (open since 3h, zero traffic)
(7,  'sess-r-001', 'uniq-r-001', 'rami.bouazizi', '10.0.0.1', '3', DATE_SUB(NOW(), INTERVAL 180 MINUTE), NULL, 10800, 0, 0, 'ISP-NAS', 'SIM-000007', 'En cours', ''),

-- S04: Nour — Session-Timeout daily (quota exhausted)
(8,  'sess-n-001', 'uniq-n-001', 'nour.sfaxi', '10.0.0.1', '4', DATE_SUB(NOW(), INTERVAL 70 MINUTE),    DATE_SUB(NOW(), INTERVAL 10 MINUTE), 3600, 5368709120, 1073741824, 'ISP-NAS', 'SIM-000008', 'Session-Timeout', ''),
(9,  'sess-n-002', 'uniq-n-002', 'nour.sfaxi', '10.0.0.1', '4', DATE_SUB(NOW(), INTERVAL 1510 MINUTE),  DATE_SUB(NOW(), INTERVAL 1450 MINUTE), 3600, 5368709120, 1073741824, 'ISP-NAS', 'SIM-000008', 'Session-Timeout', ''),
(10, 'sess-n-003', 'uniq-n-003', 'nour.sfaxi', '10.0.0.1', '4', DATE_SUB(NOW(), INTERVAL 2950 MINUTE),  DATE_SUB(NOW(), INTERVAL 2890 MINUTE), 3600, 5368709120, 1073741824, 'ISP-NAS', 'SIM-000008', 'Session-Timeout', ''),

-- S05: Tarek — Lost-Carrier every ~3h (firmware bug)
(11, 'sess-t-001', 'uniq-t-001', 'tarek.belhaj', '10.0.0.1', '5', DATE_SUB(NOW(), INTERVAL 30 MINUTE),  NULL, 1800, 102400000, 51200000, 'ISP-NAS', 'SIM-000009', 'En cours', '100.64.9.5'),
(12, 'sess-t-002', 'uniq-t-002', 'tarek.belhaj', '10.0.0.1', '5', DATE_SUB(NOW(), INTERVAL 210 MINUTE), DATE_SUB(NOW(), INTERVAL 30 MINUTE), 10800, 204800000, 102400000, 'ISP-NAS', 'SIM-000009', 'Lost-Carrier', '100.64.9.5'),
(13, 'sess-t-003', 'uniq-t-003', 'tarek.belhaj', '10.0.0.1', '5', DATE_SUB(NOW(), INTERVAL 390 MINUTE), DATE_SUB(NOW(), INTERVAL 210 MINUTE), 10800, 204800000, 102400000, 'ISP-NAS', 'SIM-000009', 'Lost-Carrier', '100.64.9.5'),
(14, 'sess-t-004', 'uniq-t-004', 'tarek.belhaj', '10.0.0.1', '5', DATE_SUB(NOW(), INTERVAL 570 MINUTE), DATE_SUB(NOW(), INTERVAL 390 MINUTE), 10800, 204800000, 102400000, 'ISP-NAS', 'SIM-000009', 'Lost-Carrier', '100.64.9.5'),

-- S06: Yasmine — connected, stable (MTU issue, not disconnect)
(15, 'sess-y-001', 'uniq-y-001', 'yasmine.karray', '10.0.0.1', '6', DATE_SUB(NOW(), INTERVAL 60 MINUTE), NULL, 7200, 512000000, 256000000, 'ISP-NAS', 'SIM-000010', 'En cours', '100.64.10.2'),

-- S07: Amine — no sessions (never connected successfully)
(16, 'sess-a-001', 'uniq-a-001', 'amine.gharbi', '10.0.0.1', '7', DATE_SUB(NOW(), INTERVAL 20 MINUTE), NULL, NULL, 0, 0, 'ISP-NAS', 'SIM-000011', 'Access-Reject', ''),

-- S08: Sarra — Lost-Carrier 6x in last 24h (line instability)
(17, 'sess-sa-001', 'uniq-sa-001', 'sarra.mejri', '10.0.0.1', '8', DATE_SUB(NOW(), INTERVAL 90 MINUTE),  NULL, 0, 1024000, 512000, 'ISP-NAS', 'SIM-000012', 'En cours', '100.64.12.1'),
(18, 'sess-sa-002', 'uniq-sa-002', 'sarra.mejri', '10.0.0.1', '8', DATE_SUB(NOW(), INTERVAL 180 MINUTE), DATE_SUB(NOW(), INTERVAL 90 MINUTE),  5400,  1024000, 512000, 'ISP-NAS', 'SIM-000012', 'Lost-Carrier', '100.64.12.1'),
(19, 'sess-sa-003', 'uniq-sa-003', 'sarra.mejri', '10.0.0.1', '8', DATE_SUB(NOW(), INTERVAL 270 MINUTE), DATE_SUB(NOW(), INTERVAL 180 MINUTE), 5400,  1024000, 512000, 'ISP-NAS', 'SIM-000012', 'Lost-Carrier', '100.64.12.1'),
(20, 'sess-sa-004', 'uniq-sa-004', 'sarra.mejri', '10.0.0.1', '8', DATE_SUB(NOW(), INTERVAL 360 MINUTE), DATE_SUB(NOW(), INTERVAL 270 MINUTE), 5400,  1024000, 512000, 'ISP-NAS', 'SIM-000012', 'Lost-Carrier', '100.64.12.1'),
(21, 'sess-sa-005', 'uniq-sa-005', 'sarra.mejri', '10.0.0.1', '8', DATE_SUB(NOW(), INTERVAL 540 MINUTE), DATE_SUB(NOW(), INTERVAL 360 MINUTE), 10800, 2048000, 1024000, 'ISP-NAS', 'SIM-000012', 'Lost-Carrier', '100.64.12.1'),
(22, 'sess-sa-006', 'uniq-sa-006', 'sarra.mejri', '10.0.0.1', '8', DATE_SUB(NOW(), INTERVAL 720 MINUTE), DATE_SUB(NOW(), INTERVAL 540 MINUTE), 10800, 2048000, 1024000, 'ISP-NAS', 'SIM-000012', 'Lost-Carrier', '100.64.12.1'),
(23, 'sess-sa-007', 'uniq-sa-007', 'sarra.mejri', '10.0.0.1', '8', DATE_SUB(NOW(), INTERVAL 900 MINUTE), DATE_SUB(NOW(), INTERVAL 720 MINUTE), 10800, 2048000, 1024000, 'ISP-NAS', 'SIM-000012', 'Lost-Carrier', '100.64.12.1'),

-- S09: Mehdi — short sessions 15-30min (intermittent disconnections)
(24, 'sess-me-001', 'uniq-me-001', 'mehdi.triki', '10.0.0.1', '9', DATE_SUB(NOW(), INTERVAL 60 MINUTE),  NULL, 0, 512000, 256000, 'ISP-NAS', 'SIM-000013', 'En cours', '100.64.13.1'),
(25, 'sess-me-002', 'uniq-me-002', 'mehdi.triki', '10.0.0.1', '9', DATE_SUB(NOW(), INTERVAL 120 MINUTE), DATE_SUB(NOW(), INTERVAL 60 MINUTE),  1800, 512000, 256000, 'ISP-NAS', 'SIM-000013', 'Lost-Carrier', '100.64.13.1'),
(26, 'sess-me-003', 'uniq-me-003', 'mehdi.triki', '10.0.0.1', '9', DATE_SUB(NOW(), INTERVAL 180 MINUTE), DATE_SUB(NOW(), INTERVAL 120 MINUTE), 1800, 512000, 256000, 'ISP-NAS', 'SIM-000013', 'Lost-Carrier', '100.64.13.1'),
(27, 'sess-me-004', 'uniq-me-004', 'mehdi.triki', '10.0.0.1', '9', DATE_SUB(NOW(), INTERVAL 270 MINUTE), DATE_SUB(NOW(), INTERVAL 180 MINUTE), 900,  256000, 128000, 'ISP-NAS', 'SIM-000013', 'Lost-Carrier', '100.64.13.1'),
(28, 'sess-me-005', 'uniq-me-005', 'mehdi.triki', '10.0.0.1', '9', DATE_SUB(NOW(), INTERVAL 330 MINUTE), DATE_SUB(NOW(), INTERVAL 270 MINUTE), 900,  256000, 128000, 'ISP-NAS', 'SIM-000013', 'NAS-Error',   '100.64.13.1'),

-- S10: Ines — connected, long sessions (speed issue, not disconnect)
(29, 'sess-in-001', 'uniq-in-001', 'ines.chaabane', '10.0.0.1', '10', DATE_SUB(NOW(), INTERVAL 120 MINUTE), NULL, 7200, 1073741824, 536870912, 'ISP-NAS', 'SIM-000014', 'En cours', '100.64.14.1'),

-- S11: Omar — rejected (suspended account)
(30, 'sess-om-001', 'uniq-om-001', 'omar.hadj', '10.0.0.1', '11', DATE_SUB(NOW(), INTERVAL 30 MINUTE), NULL, NULL, 0, 0, 'ISP-NAS', 'SIM-000015', 'Access-Reject', ''),
(31, 'sess-om-002', 'uniq-om-002', 'omar.hadj', '10.0.0.1', '11', DATE_SUB(NOW(), INTERVAL 20 MINUTE), NULL, NULL, 0, 0, 'ISP-NAS', 'SIM-000015', 'Access-Reject', ''),

-- S12: Rim — connected but high latency (not disconnect issue)
(32, 'sess-ri-001', 'uniq-ri-001', 'rim.belhadj', '10.0.0.1', '12', DATE_SUB(NOW(), INTERVAL 45 MINUTE), NULL, 2700, 2097152000, 1048576000, 'ISP-NAS', 'SIM-000016', 'En cours', '100.64.16.1'),

-- S13: Bilel — hits Session-Timeout daily
(33, 'sess-bi-001', 'uniq-bi-001', 'bilel.amor', '10.0.0.1', '13', DATE_SUB(NOW(), INTERVAL 80 MINUTE),   DATE_SUB(NOW(), INTERVAL 20 MINUTE),  3600, 5368709120, 1073741824, 'ISP-NAS', 'SIM-000017', 'Session-Timeout', ''),
(34, 'sess-bi-002', 'uniq-bi-002', 'bilel.amor', '10.0.0.1', '13', DATE_SUB(NOW(), INTERVAL 1520 MINUTE), DATE_SUB(NOW(), INTERVAL 1460 MINUTE), 3600, 5368709120, 1073741824, 'ISP-NAS', 'SIM-000017', 'Session-Timeout', ''),
(35, 'sess-bi-003', 'uniq-bi-003', 'bilel.amor', '10.0.0.1', '13', DATE_SUB(NOW(), INTERVAL 2960 MINUTE), DATE_SUB(NOW(), INTERVAL 2900 MINUTE), 3600, 5368709120, 1073741824, 'ISP-NAS', 'SIM-000017', 'Session-Timeout', ''),

-- S14: Dorra — connected, stable (WiFi issue not line)
(36, 'sess-do-001', 'uniq-do-001', 'dorra.miled', '10.0.0.1', '14', DATE_SUB(NOW(), INTERVAL 60 MINUTE), NULL, 3600, 2147483648, 1073741824, 'ISP-NAS', 'SIM-000018', 'En cours', '100.64.18.1'),

-- S15: Sofien — recent disconnections, PPPoE failures after reboot
(37, 'sess-so-001', 'uniq-so-001', 'sofien.nasr', '10.0.0.1', '15', DATE_SUB(NOW(), INTERVAL 40 MINUTE), NULL, NULL, 0, 0, 'ISP-NAS', 'SIM-000019', 'Access-Reject', ''),
(38, 'sess-so-002', 'uniq-so-002', 'sofien.nasr', '10.0.0.1', '15', DATE_SUB(NOW(), INTERVAL 90 MINUTE), DATE_SUB(NOW(), INTERVAL 40 MINUTE), 3000, 102400, 51200, 'ISP-NAS', 'SIM-000019', 'Lost-Carrier', ''),
(39, 'sess-so-003', 'uniq-so-003', 'sofien.nasr', '10.0.0.1', '15', DATE_SUB(NOW(), INTERVAL 180 MINUTE), DATE_SUB(NOW(), INTERVAL 90 MINUTE), 5400, 204800, 102400, 'ISP-NAS', 'SIM-000019', 'Lost-Carrier', '');

-- ============================================================
-- USERS — Customer directory
-- Links CIN/phone → username → serial_number
-- ============================================================

INSERT INTO `users` (`id`, `cin`, `full_name`, `phone`, `email`, `address`, `username`, `serial_number`, `status`) VALUES
-- Original 6 scenario customers
(1,  '11223344', 'Karim Mansouri',  '+21600000001', 'karim@example.tn',   '1 Demo Street, Sousse',              'karim.mansouri',  'SIM-000005', 'active'),
(2,  '22334455', 'Leila Hamdi',     '+21600000002', 'leila@example.tn',   '2 Demo Street, Monastir',      'leila.hamdi',     'SIM-000006', 'active'),
(3,  '33445566', 'Rami Bouazizi',   '+21600000003', 'rami@example.tn',    '3 Demo Street, Sfax',                    'rami.bouazizi',   'SIM-000007', 'active'),
(4,  '44556677', 'Nour Sfaxi',      '+21600000004', 'nour@example.tn',    '4 Demo Street, Kairouan',            'nour.sfaxi',      'SIM-000008', 'active'),
(5,  '55667788', 'Tarek Belhaj',    '+21600000005', 'tarek@example.tn',   '5 Demo Street, Gabès',        'tarek.belhaj',    'SIM-000009', 'active'),
(6,  '66778899', 'Yasmine Karray',  '+21600000006', 'yasmine@example.tn', '6 Demo Street, Tunis',                 'yasmine.karray',  'SIM-000010', 'active'),
-- New scenario customers
(7,  '77889900', 'Amine Gharbi',    '+21600000007', 'amine@example.tn',   '7 Demo Street, Bizerte',         'amine.gharbi',    'SIM-000011', 'active'),
(8,  '88990011', 'Sarra Mejri',     '+21600000008', 'sarra@example.tn',   '8 Demo Street, Nabeul',             'sarra.mejri',     'SIM-000012', 'active'),
(9,  '99001122', 'Mehdi Triki',     '+21600000009', 'mehdi@example.tn',   '9 Demo Street, Ariana',                'mehdi.triki',     'SIM-000013', 'active'),
(10, '10111213', 'Ines Chaabane',   '+21600000010', 'ines@example.tn',    '10 Demo Street, Tunis',             'ines.chaabane',   'SIM-000014', 'active'),
(11, '14151617', 'Omar Hadj',       '+21600000011', 'omar@example.tn',    '11 Demo Street, Sousse',             'omar.hadj',       'SIM-000015', 'suspended'),
(12, '18192021', 'Rim Belhadj',     '+21600000012', 'rim@example.tn',     '12 Demo Street, Sfax',                'rim.belhadj',     'SIM-000016', 'active'),
(13, '22232425', 'Bilel Amor',      '+21600000013', 'bilel@example.tn',   '13 Demo Street, Tunis',               'bilel.amor',      'SIM-000017', 'active'),
(14, '26272829', 'Dorra Miled',     '+21600000014', 'dorra@example.tn',   '14 Demo Street, Manouba',              'dorra.miled',     'SIM-000018', 'active'),
(15, '30313233', 'Sofien Nasr',     '+21600000015', 'sofien@example.tn',  '15 Demo Street, Jendouba',           'sofien.nasr',     'SIM-000019', 'active');

-- ============================================================
-- NETWORK_METRICS — Performance measurements per customer
-- Used by get_network_metrics() for slow speed / latency diagnosis
-- ============================================================

INSERT INTO `network_metrics` (`id`, `username`, `avg_download_mbps`, `avg_upload_mbps`, `latency_ms`, `packet_loss`, `timestamp`) VALUES
-- S01: Karim — no metrics (never connected successfully)
-- S02: Leila — degraded speed (SNR issue)
(1,  'leila.hamdi',    2.1,  0.5,  85,  1.5, DATE_SUB(NOW(), INTERVAL 30 MINUTE)),
-- S03: Rami — normal metrics (zombie session, not performance)
(2,  'rami.bouazizi',  45.0, 9.0,  15,  0.1, DATE_SUB(NOW(), INTERVAL 60 MINUTE)),
-- S04: Nour — normal speed before quota hit
(3,  'nour.sfaxi',     7.5,  1.8,  25,  0.2, DATE_SUB(NOW(), INTERVAL 90 MINUTE)),
-- S05: Tarek — normal speed (firmware issue, not speed)
(4,  'tarek.belhaj',   38.0, 7.5,  20,  0.3, DATE_SUB(NOW(), INTERVAL 45 MINUTE)),
-- S06: Yasmine — good speed (MTU affects large transfers, not speed test)
(5,  'yasmine.karray', 47.0, 9.5,  12,  0.1, DATE_SUB(NOW(), INTERVAL 30 MINUTE)),
-- S07: Amine — no metrics (never connected)
-- S08: Sarra — degraded (Lost-Carrier instability)
(6,  'sarra.mejri',    3.5,  0.8,  95,  2.8, DATE_SUB(NOW(), INTERVAL 45 MINUTE)),
-- S09: Mehdi — marginal metrics (intermittent)
(7,  'mehdi.triki',    5.2,  1.2,  120, 3.2, DATE_SUB(NOW(), INTERVAL 30 MINUTE)),
-- S10: Ines — speed well below plan (premium = 50Mbps, getting 12Mbps)
(8,  'ines.chaabane',  12.0, 2.5,  35,  0.5, DATE_SUB(NOW(), INTERVAL 20 MINUTE)),
-- S11: Omar — no metrics (suspended)
-- S12: Rim — high latency / packet loss
(9,  'rim.belhadj',    44.0, 8.5,  185, 4.2, DATE_SUB(NOW(), INTERVAL 15 MINUTE)),
-- S13: Bilel — normal speed before quota hit
(10, 'bilel.amor',     7.2,  1.7,  28,  0.2, DATE_SUB(NOW(), INTERVAL 60 MINUTE)),
-- S14: Dorra — excellent line (WiFi issue is local, line is perfect)
(11, 'dorra.miled',    48.0, 9.8,  8,   0.0, DATE_SUB(NOW(), INTERVAL 30 MINUTE)),
-- S15: Sofien — poor metrics (misconfigured device)
(12, 'sofien.nasr',    0.5,  0.1,  250, 8.5, DATE_SUB(NOW(), INTERVAL 20 MINUTE));

-- ============================================================
-- OUTAGES — Regional outage data
-- One active outage (Nord-Ouest) for outage scenario testing
-- One resolved outage (Centre-Est) for history
-- ============================================================

INSERT INTO `outages` (`id`, `region`, `governorates`, `description`, `status`, `estimated_fix`, `created_at`) VALUES
(1, 'Nord-Ouest',
   'Bizerte,Béja,Jendouba,Kef,Siliana',
   'Panne sur le nœud de distribution fibre optique de la région Nord-Ouest. Les équipes techniques sont en cours d''intervention. Les clients des gouvernorats de Bizerte, Béja, Jendouba, Kef et Siliana sont affectés.',
   'active',
   DATE_ADD(NOW(), INTERVAL 2 HOUR),
   DATE_SUB(NOW(), INTERVAL 1 HOUR)),
(2, 'Centre-Est',
   'Sousse,Monastir,Mahdia,Kairouan',
   'Interruption de service suite à une coupure électrique au niveau du datacenter régional de Sousse. Problème résolu.',
   'resolved',
   DATE_SUB(NOW(), INTERVAL 3 HOUR),
   DATE_SUB(NOW(), INTERVAL 8 HOUR));

-- ============================================================
-- TICKETS — Pre-existing support tickets (history)
-- ============================================================

INSERT INTO `tickets` (`id`, `ticket_number`, `customer_username`, `customer_name`, `customer_phone`, `problem_description`, `status`, `priority`, `created_at`) VALUES
(1, 'TKT-000001', 'leila.hamdi',  'Leila Hamdi',  '+21600000002', '[Déconnexion physique] SNR faible (4 dB), déconnexions NAS-Error répétées. Intervention technicien requise.', 'open',        'high',   DATE_SUB(NOW(), INTERVAL 2 DAY)),
(2, 'TKT-000002', 'tarek.belhaj', 'Tarek Belhaj', '+21600000005', '[Firmware obsolète] Version 2.1.0 < minimum 3.0.0. Lost-Carrier toutes les 3h. Mise à jour firmware requise.',  'in_progress', 'high',   DATE_SUB(NOW(), INTERVAL 1 DAY)),
(3, 'TKT-000003', 'sofien.nasr',  'Sofien Nasr',  '+21600000015', '[Problème connexion persistant] Reboot et push PPPoE échoués. Configuration routeur corrompue.',               'open',        'high',   DATE_SUB(NOW(), INTERVAL 3 HOUR));