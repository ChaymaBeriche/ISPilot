-- ============================================================
-- schema.sql — FreeRADIUS + ISP Agent Database Schema
-- ============================================================

CREATE DATABASE IF NOT EXISTS radius;
USE radius;

-- ── FreeRADIUS core tables ────────────────────────────────────

CREATE TABLE IF NOT EXISTS radcheck (
  id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
  username  VARCHAR(64)  NOT NULL DEFAULT '',
  attribute VARCHAR(64)  NOT NULL DEFAULT '',
  op        VARCHAR(2)   NOT NULL DEFAULT ':=',
  value     VARCHAR(253) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  UNIQUE KEY idx_radcheck_username (username),
  KEY username (username(32))
);

CREATE TABLE IF NOT EXISTS radreply (
  id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
  username  VARCHAR(64)  NOT NULL DEFAULT '',
  attribute VARCHAR(64)  NOT NULL DEFAULT '',
  op        VARCHAR(2)   NOT NULL DEFAULT '=',
  value     VARCHAR(253) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  KEY username (username(32))
);

CREATE TABLE IF NOT EXISTS radgroupcheck (
  id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
  groupname VARCHAR(64)  NOT NULL DEFAULT '',
  attribute VARCHAR(64)  NOT NULL DEFAULT '',
  op        VARCHAR(2)   NOT NULL DEFAULT ':=',
  value     VARCHAR(253) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  KEY groupname (groupname(32))
);

CREATE TABLE IF NOT EXISTS radgroupreply (
  id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
  groupname VARCHAR(64)  NOT NULL DEFAULT '',
  attribute VARCHAR(64)  NOT NULL DEFAULT '',
  op        VARCHAR(2)   NOT NULL DEFAULT '=',
  value     VARCHAR(253) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  KEY groupname (groupname(32))
);

CREATE TABLE IF NOT EXISTS radusergroup (
  id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
  username  VARCHAR(64)  NOT NULL DEFAULT '',
  groupname VARCHAR(64)  NOT NULL DEFAULT '',
  priority  INT          NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY username (username(32))
);

CREATE TABLE IF NOT EXISTS radacct (
  radacctid          BIGINT       NOT NULL AUTO_INCREMENT,
  acctsessionid      VARCHAR(64)  NOT NULL DEFAULT '',
  acctuniqueid       VARCHAR(32)  NOT NULL DEFAULT '',
  username           VARCHAR(64)  NOT NULL DEFAULT '',
  nasipaddress       VARCHAR(15)  NOT NULL DEFAULT '',
  nasportid          VARCHAR(15)  DEFAULT NULL,
  nasporttype        VARCHAR(32)  DEFAULT NULL,
  acctstarttime      DATETIME     DEFAULT NULL,
  acctstoptime       DATETIME     DEFAULT NULL,
  acctsessiontime    INT UNSIGNED DEFAULT NULL,
  acctauthentic      VARCHAR(32)  DEFAULT NULL,
  acctinputoctets    BIGINT       DEFAULT NULL,
  acctoutputoctets   BIGINT       DEFAULT NULL,
  calledstationid    VARCHAR(50)  NOT NULL DEFAULT '',
  callingstationid   VARCHAR(50)  NOT NULL DEFAULT '',
  acctterminatecause VARCHAR(32)  NOT NULL DEFAULT '',
  servicetype        VARCHAR(32)  DEFAULT NULL,
  framedprotocol     VARCHAR(32)  DEFAULT NULL,
  framedipaddress    VARCHAR(15)  NOT NULL DEFAULT '',
  PRIMARY KEY (radacctid),
  UNIQUE KEY acctuniqueid (acctuniqueid),
  KEY username (username),
  KEY framedipaddress (framedipaddress),
  KEY acctsessionid (acctsessionid),
  KEY acctsessiontime (acctsessiontime),
  KEY acctstarttime (acctstarttime),
  KEY nasipaddress (nasipaddress)
);

CREATE TABLE IF NOT EXISTS nas (
  id          INT          NOT NULL AUTO_INCREMENT,
  nasname     VARCHAR(128) NOT NULL,
  shortname   VARCHAR(32)  DEFAULT NULL,
  type        VARCHAR(30)  DEFAULT 'other',
  ports       INT          DEFAULT NULL,
  secret      VARCHAR(60)  NOT NULL DEFAULT 'CHANGE_ME_demo',
  server      VARCHAR(64)  DEFAULT NULL,
  community   VARCHAR(50)  DEFAULT NULL,
  description VARCHAR(200) DEFAULT 'RADIUS Client',
  PRIMARY KEY (id),
  KEY nasname (nasname(32))
);

CREATE TABLE IF NOT EXISTS radpostauth (
  id       INT         NOT NULL AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL DEFAULT '',
  pass     VARCHAR(64) NOT NULL DEFAULT '',
  reply    VARCHAR(32) NOT NULL DEFAULT '',
  authdate TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
);

-- ── ISP Agent tables ──────────────────────────────────────────

-- Customer directory
-- Links CIN/phone → RADIUS username → router serial number
CREATE TABLE IF NOT EXISTS users (
  id            INT          NOT NULL AUTO_INCREMENT,
  cin           VARCHAR(20)  NOT NULL,
  full_name     VARCHAR(100) NOT NULL,
  phone         VARCHAR(20)  DEFAULT NULL,
  email         VARCHAR(100) DEFAULT NULL,
  address       TEXT         DEFAULT NULL,
  username      VARCHAR(64)  NOT NULL,
  serial_number VARCHAR(50)  DEFAULT NULL,       -- GenieACS device serial
  status        ENUM('active','suspended','expired','pending','cancelled')
                             DEFAULT 'active',
  created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY cin (cin),
  UNIQUE KEY username (username),
  CONSTRAINT fk_users_username FOREIGN KEY (username)
    REFERENCES radcheck(username)
    ON UPDATE CASCADE
    ON DELETE CASCADE
);

-- Regional outages
-- Used by check_region_outage() to detect active outages before diagnosis
CREATE TABLE IF NOT EXISTS outages (
  id            INT          NOT NULL AUTO_INCREMENT,
  region        VARCHAR(100) NOT NULL,
  governorates  TEXT         NOT NULL,           -- comma-separated list
  description   TEXT         NOT NULL,
  status        ENUM('active','resolved')        DEFAULT 'active',
  estimated_fix DATETIME     DEFAULT NULL,
  created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
);

-- Support tickets
-- Created by create_ticket() for human technician escalation
CREATE TABLE IF NOT EXISTS tickets (
  id                  INT          NOT NULL AUTO_INCREMENT,
  ticket_number       VARCHAR(30)  NOT NULL,
  customer_username   VARCHAR(64)  NOT NULL,
  customer_name       VARCHAR(100) DEFAULT NULL,
  customer_phone      VARCHAR(20)  DEFAULT NULL,
  problem_description TEXT         DEFAULT NULL,
  diagnosis           TEXT         DEFAULT NULL,
  steps_tried         TEXT         DEFAULT NULL,
  status              ENUM('open','in_progress','resolved','closed')
                                   DEFAULT 'open',
  priority            ENUM('low','medium','high','critical')
                                   DEFAULT 'medium',
  created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY ticket_number (ticket_number)
);

-- Network performance metrics
-- Used by get_network_metrics() for speed/latency diagnosis
CREATE TABLE IF NOT EXISTS network_metrics (
  id                INT   NOT NULL AUTO_INCREMENT,
  username          VARCHAR(64)  DEFAULT NULL,
  avg_download_mbps FLOAT        DEFAULT NULL,
  avg_upload_mbps   FLOAT        DEFAULT NULL,
  latency_ms        INT          DEFAULT NULL,
  packet_loss       FLOAT        DEFAULT NULL,
  timestamp         DATETIME     DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
);