DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'slack-mm') THEN
        CREATE USER "slack-mm" WITH PASSWORD 'slack-mm';
    END IF;
END $$;

SELECT 'CREATE DATABASE mattermost OWNER "slack-mm"' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mattermost')\gexec

GRANT ALL PRIVILEGES ON DATABASE mattermost TO "slack-mm";

\connect mattermost

-- Создание таблиц (минимально необходимые)
CREATE TABLE IF NOT EXISTS public.teams (
    id varchar(26) PRIMARY KEY,
    createat bigint,
    updateat bigint,
    deleteat bigint,
    displayname varchar(64),
    name varchar(64),
    description varchar(255),
    email varchar(128),
    type varchar(255),
    companyname varchar(64),
    alloweddomains varchar(500),
    inviteid varchar(32),
    schemeid varchar(26),
    allowopeninvite varchar(5),
    lastteamiconupdate bigint,
    groupconstrained boolean,
    cloudlimitsarchived varchar(5)
);

CREATE TABLE IF NOT EXISTS public.users (
    id varchar(26) PRIMARY KEY,
    createat bigint,
    updateat bigint,
    deleteat bigint,
    username varchar(64),
    password varchar(128),
    authdata varchar(128),
    authservice varchar(32),
    email varchar(128),
    emailverified boolean,
    nickname varchar(64),
    firstname varchar(64),
    lastname varchar(64),
    roles varchar(256),
    allowmarketing boolean,
    props jsonb,
    notifyprops jsonb,
    lastpasswordupdate bigint,
    lastpictureupdate bigint,
    failedattempts int,
    locale varchar(5),
    mfaactive boolean,
    mfasecret varchar(128),
    position varchar(128),
    timezone jsonb,
    remoteid varchar(26),
    lastlogin bigint DEFAULT 0 NOT NULL,
    mfausedtimestamps jsonb
);

CREATE TABLE IF NOT EXISTS public.useraccesstokens (
    id varchar(26) PRIMARY KEY,
    token varchar(64),
    userid varchar(26),
    description varchar(255),
    isactive boolean
);

CREATE TABLE IF NOT EXISTS public.teammembers (
    teamid varchar(26),
    userid varchar(26),
    roles varchar(256),
    deleteat bigint,
    schemeuser boolean,
    schemeadmin boolean,
    schemeguest boolean,
    createat bigint
);

-- Создание таблицы каналов (максимально совместимо с Mattermost)
CREATE TABLE IF NOT EXISTS public.channels (
    id character varying(26) PRIMARY KEY,
    createat bigint,
    updateat bigint,
    deleteat bigint,
    teamid character varying(26),
    type character varying(1),
    displayname character varying(64),
    name character varying(64),
    header character varying(1024),
    purpose character varying(250),
    lastpostat bigint,
    totalmsgcount bigint,
    extraupdateat bigint,
    creatorid character varying(26),
    schemeid character varying(26),
    groupconstrained boolean,
    shared boolean,
    totalmsgcountroot bigint,
    lastrootpostat bigint DEFAULT 0,
    bannerinfo jsonb,
    defaultcategoryname character varying(64) NOT NULL DEFAULT ''
);

-- Создание таблицы участников каналов (максимально совместимо с Mattermost)
CREATE TABLE IF NOT EXISTS public.channelmembers (
    channelid character varying(26) NOT NULL,
    userid character varying(26) NOT NULL,
    roles character varying(256),
    lastviewedat bigint,
    msgcount bigint,
    mentioncount bigint,
    notifyprops jsonb,
    lastupdateat bigint,
    schemeuser boolean,
    schemeadmin boolean,
    schemeguest boolean,
    mentioncountroot bigint,
    msgcountroot bigint,
    urgentmentioncount bigint,
    PRIMARY KEY (channelid, userid)
);

-- Вставка команды, если не существует
INSERT INTO public.teams (id, createat, updateat, deleteat, displayname, name, description, email, type, companyname, alloweddomains, inviteid, schemeid, allowopeninvite, lastteamiconupdate, groupconstrained, cloudlimitsarchived)
SELECT 'b7u9rycm43nip86mdiuqsxdcbe', 1750986469595, 1750986469595, 0, 'Test', 'test', '', 'admin@admin', 'O', '', '', 'am5tg9c1sfdk3g7jicixp5pqjy', '', 'f', 0, 'f', 'f'
WHERE NOT EXISTS (SELECT 1 FROM public.teams WHERE id = 'b7u9rycm43nip86mdiuqsxdcbe');

-- Вставка пользователя, если не существует
INSERT INTO public.users (id, createat, updateat, deleteat, username, password, authdata, authservice, email, emailverified, nickname, firstname, lastname, roles, allowmarketing, props, notifyprops, lastpasswordupdate, lastpictureupdate, failedattempts, locale, mfaactive, mfasecret, "position", timezone, remoteid, lastlogin, mfausedtimestamps)
SELECT 'o6b98rc1tpnfmy7ajxiadygmzy', 1750986450008, 1750986469609, 0, 'admin', '$2a$10$gSm/am2weKxS06Dzvgqu../d9CWwh8nHXnBrdBPrpHN2v.uW4h/de', '', '', 'is@careerum.com', 'f', '', '', '', 'system_admin system_user', 'f', '{}', '{"push": "mention", "email": "true", "channel": "true", "desktop": "mention", "comments": "never", "first_name": "false", "push_status": "online", "mention_keys": "", "push_threads": "all", "desktop_sound": "true", "email_threads": "all", "desktop_threads": "all"}', 1750986450008, 0, 0, 'en', 'f', '', '', '{"manualTimezone": "", "automaticTimezone": "Europe/Berlin", "useAutomaticTimezone": "true"}', '', 1750986450144, '[]'
WHERE NOT EXISTS (SELECT 1 FROM public.users WHERE id = 'o6b98rc1tpnfmy7ajxiadygmzy');

-- Вставка токена пользователя, если не существует
INSERT INTO public.useraccesstokens (id, token, userid, description, isactive)
SELECT 'xr79x5xhdff3uc5ohy4m1hknmr', '5x7rr788c7gwdnkdr9imb49ffo', 'o6b98rc1tpnfmy7ajxiadygmzy', 'test', true
WHERE NOT EXISTS (SELECT 1 FROM public.useraccesstokens WHERE id = 'xr79x5xhdff3uc5ohy4m1hknmr');

-- Вставка участника команды, если не существует
INSERT INTO public.teammembers (teamid, userid, roles, deleteat, schemeuser, schemeadmin, schemeguest, createat)
SELECT 'b7u9rycm43nip86mdiuqsxdcbe', 'o6b98rc1tpnfmy7ajxiadygmzy', '', 0, true, true, false, 1750986469605
WHERE NOT EXISTS (SELECT 1 FROM public.teammembers WHERE teamid = 'b7u9rycm43nip86mdiuqsxdcbe' AND userid = 'o6b98rc1tpnfmy7ajxiadygmzy'); 

-- Канал
INSERT INTO public.channels (id, createat, updateat, deleteat, teamid, type, displayname, name, header, purpose, lastpostat, totalmsgcount, extraupdateat, creatorid, schemeid, groupconstrained, shared, totalmsgcountroot, lastrootpostat, bannerinfo, defaultcategoryname)
SELECT '8q3dynerq7nzzmxo8dfckcfdnr', 1750986828119, 1750986828119, 0, 'b7u9rycm43nip86mdiuqsxdcbe', 'O', 'Test Channel 1', 'test-channel-1', '', 'Channel for integration testing', 1750986828128, 0, 0, 'o6b98rc1tpnfmy7ajxiadygmzy', NULL, NULL, NULL, 0, 1750986828128, NULL, ''
WHERE NOT EXISTS (
    SELECT 1 FROM public.channels WHERE id = '8q3dynerq7nzzmxo8dfckcfdnr'
);

-- Участник канала
INSERT INTO public.channelmembers (channelid, userid, roles, lastviewedat, msgcount, mentioncount, notifyprops, lastupdateat, schemeuser, schemeadmin, schemeguest, mentioncountroot, msgcountroot, urgentmentioncount)
SELECT '8q3dynerq7nzzmxo8dfckcfdnr', 'o6b98rc1tpnfmy7ajxiadygmzy', '', 0, 0, 0, '{"push": "default", "email": "default", "desktop": "default", "mark_unread": "all", "ignore_channel_mentions": "default", "channel_auto_follow_threads": "off"}', 1750986828123, true, true, false, 0, 0, 0
WHERE NOT EXISTS (
    SELECT 1 FROM public.channelmembers WHERE channelid = '8q3dynerq7nzzmxo8dfckcfdnr' AND userid = 'o6b98rc1tpnfmy7ajxiadygmzy'
);
