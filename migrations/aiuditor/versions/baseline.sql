-- Frozen schema-only baseline at 0053_onboarding_runs. No seed data.
SET LOCAL search_path = public, pg_catalog;
SET LOCAL check_function_bodies = false;

-- TABLE: aiuditor_admissions
CREATE TABLE public.aiuditor_admissions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    file_hash character varying(256) NOT NULL,
    format character varying(32) NOT NULL,
    quant character varying(32),
    license character varying(64) NOT NULL,
    intended_class character varying(64) NOT NULL,
    credential_ref character varying(128),
    status character varying(16) NOT NULL,
    verdict character varying(16),
    reason character varying(120),
    resolved_by character varying(256),
    size_bytes bigint,
    sandbox_soak_ok boolean,
    sandbox_egress_ok boolean,
    sandbox_egress_reason character varying(64),
    created_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone
);

ALTER TABLE ONLY public.aiuditor_admissions FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_approvals
CREATE TABLE public.aiuditor_approvals (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    lease_id uuid,
    connection_id uuid,
    purpose character varying(32) NOT NULL,
    who character varying(256) NOT NULL,
    why character varying(16) NOT NULL,
    prompt_class character varying(64),
    status character varying(16) NOT NULL,
    decision character varying(32),
    resolved_by character varying(256),
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    consumed_at timestamp with time zone
);

ALTER TABLE ONLY public.aiuditor_approvals FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_candidates
CREATE TABLE public.aiuditor_candidates (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    repo character varying(256) NOT NULL,
    revision character varying(128) NOT NULL,
    file_name character varying(256) NOT NULL,
    size_bytes bigint NOT NULL,
    license character varying(64),
    quant character varying(32),
    file_hash character varying(256),
    publisher character varying(64) NOT NULL,
    catalog_id character varying(64) NOT NULL,
    class_id character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    soak_job_id character varying(64),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.aiuditor_candidates FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_catalog
CREATE TABLE public.aiuditor_catalog (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    title character varying(120) NOT NULL,
    backend character varying(32) NOT NULL,
    class_id character varying(64),
    catalog_id character varying(64),
    connection_id uuid,
    meters jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.aiuditor_catalog FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_connections
CREATE TABLE public.aiuditor_connections (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    kind character varying(16) NOT NULL,
    purpose character varying(16) NOT NULL,
    device_id uuid,
    endpoint character varying(2048),
    credential_ref character varying(128),
    lease_id uuid,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.aiuditor_connections FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_contracts
CREATE TABLE public.aiuditor_contracts (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    parties jsonb NOT NULL,
    meters jsonb NOT NULL,
    rail character varying(32) NOT NULL,
    until timestamp with time zone NOT NULL,
    dispute_tip character varying(120),
    connection_id uuid,
    lease_id uuid,
    status character varying(16) NOT NULL,
    signed_by character varying(256),
    created_at timestamp with time zone NOT NULL,
    signed_at timestamp with time zone
);

ALTER TABLE ONLY public.aiuditor_contracts FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_envelopes
CREATE TABLE public.aiuditor_envelopes (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    class_id character varying(64) NOT NULL,
    catalog_id character varying(64) NOT NULL,
    file_hash character varying(256) NOT NULL,
    quant character varying(32),
    runtime character varying(64) NOT NULL,
    context integer,
    fit boolean NOT NULL,
    decode_tok_s double precision,
    thermal_c double precision,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.aiuditor_envelopes FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_policies
CREATE TABLE public.aiuditor_policies (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    purpose character varying(32) NOT NULL,
    prompt_class character varying(64),
    connection_id uuid,
    disposition character varying(16) NOT NULL,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.aiuditor_policies FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_suite_members
CREATE TABLE public.aiuditor_suite_members (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    class_id character varying(64) NOT NULL,
    catalog_id character varying(64) NOT NULL,
    pack_id character varying(64) NOT NULL,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.aiuditor_suite_members FORCE ROW LEVEL SECURITY;


--

-- TABLE: aiuditor_violations
CREATE TABLE public.aiuditor_violations (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    contract_id uuid NOT NULL,
    lease_id uuid,
    kind character varying(32) NOT NULL,
    action character varying(16) NOT NULL,
    reason character varying(120),
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.aiuditor_violations FORCE ROW LEVEL SECURITY;


--

-- CONSTRAINT: aiuditor_admissions aiuditor_admissions_pkey
ALTER TABLE ONLY public.aiuditor_admissions
    ADD CONSTRAINT aiuditor_admissions_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_approvals aiuditor_approvals_pkey
ALTER TABLE ONLY public.aiuditor_approvals
    ADD CONSTRAINT aiuditor_approvals_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_candidates aiuditor_candidates_pkey
ALTER TABLE ONLY public.aiuditor_candidates
    ADD CONSTRAINT aiuditor_candidates_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_catalog aiuditor_catalog_pkey
ALTER TABLE ONLY public.aiuditor_catalog
    ADD CONSTRAINT aiuditor_catalog_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_connections aiuditor_connections_pkey
ALTER TABLE ONLY public.aiuditor_connections
    ADD CONSTRAINT aiuditor_connections_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_contracts aiuditor_contracts_pkey
ALTER TABLE ONLY public.aiuditor_contracts
    ADD CONSTRAINT aiuditor_contracts_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_envelopes aiuditor_envelopes_pkey
ALTER TABLE ONLY public.aiuditor_envelopes
    ADD CONSTRAINT aiuditor_envelopes_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_policies aiuditor_policies_pkey
ALTER TABLE ONLY public.aiuditor_policies
    ADD CONSTRAINT aiuditor_policies_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_suite_members aiuditor_suite_members_pkey
ALTER TABLE ONLY public.aiuditor_suite_members
    ADD CONSTRAINT aiuditor_suite_members_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_violations aiuditor_violations_pkey
ALTER TABLE ONLY public.aiuditor_violations
    ADD CONSTRAINT aiuditor_violations_pkey PRIMARY KEY (id);


--

-- CONSTRAINT: aiuditor_candidates uq_aiuditor_candidate_source_class
ALTER TABLE ONLY public.aiuditor_candidates
    ADD CONSTRAINT uq_aiuditor_candidate_source_class UNIQUE (tenant_id, repo, revision, file_name, class_id);


--

-- CONSTRAINT: aiuditor_envelopes uq_aiuditor_envelope_class_catalog
ALTER TABLE ONLY public.aiuditor_envelopes
    ADD CONSTRAINT uq_aiuditor_envelope_class_catalog UNIQUE (tenant_id, class_id, catalog_id);


--

-- CONSTRAINT: aiuditor_suite_members uq_aiuditor_suite_class_catalog
ALTER TABLE ONLY public.aiuditor_suite_members
    ADD CONSTRAINT uq_aiuditor_suite_class_catalog UNIQUE (tenant_id, class_id, catalog_id);


--

-- INDEX: ix_aiuditor_admissions_tenant
CREATE INDEX ix_aiuditor_admissions_tenant ON public.aiuditor_admissions USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_admissions_tenant_status
CREATE INDEX ix_aiuditor_admissions_tenant_status ON public.aiuditor_admissions USING btree (tenant_id, status);


--

-- INDEX: ix_aiuditor_approvals_tenant
CREATE INDEX ix_aiuditor_approvals_tenant ON public.aiuditor_approvals USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_candidates_tenant
CREATE INDEX ix_aiuditor_candidates_tenant ON public.aiuditor_candidates USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_candidates_tenant_class
CREATE INDEX ix_aiuditor_candidates_tenant_class ON public.aiuditor_candidates USING btree (tenant_id, class_id);


--

-- INDEX: ix_aiuditor_candidates_tenant_status
CREATE INDEX ix_aiuditor_candidates_tenant_status ON public.aiuditor_candidates USING btree (tenant_id, status);


--

-- INDEX: ix_aiuditor_catalog_tenant
CREATE INDEX ix_aiuditor_catalog_tenant ON public.aiuditor_catalog USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_connections_tenant
CREATE INDEX ix_aiuditor_connections_tenant ON public.aiuditor_connections USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_connections_tenant_lease
CREATE INDEX ix_aiuditor_connections_tenant_lease ON public.aiuditor_connections USING btree (tenant_id, lease_id);


--

-- INDEX: ix_aiuditor_contracts_tenant
CREATE INDEX ix_aiuditor_contracts_tenant ON public.aiuditor_contracts USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_contracts_tenant_lease
CREATE INDEX ix_aiuditor_contracts_tenant_lease ON public.aiuditor_contracts USING btree (tenant_id, lease_id);


--

-- INDEX: ix_aiuditor_envelopes_tenant
CREATE INDEX ix_aiuditor_envelopes_tenant ON public.aiuditor_envelopes USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_envelopes_tenant_class
CREATE INDEX ix_aiuditor_envelopes_tenant_class ON public.aiuditor_envelopes USING btree (tenant_id, class_id);


--

-- INDEX: ix_aiuditor_policies_tenant
CREATE INDEX ix_aiuditor_policies_tenant ON public.aiuditor_policies USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_suite_members_tenant
CREATE INDEX ix_aiuditor_suite_members_tenant ON public.aiuditor_suite_members USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_suite_members_tenant_class
CREATE INDEX ix_aiuditor_suite_members_tenant_class ON public.aiuditor_suite_members USING btree (tenant_id, class_id);


--

-- INDEX: ix_aiuditor_violations_tenant
CREATE INDEX ix_aiuditor_violations_tenant ON public.aiuditor_violations USING btree (tenant_id);


--

-- INDEX: ix_aiuditor_violations_tenant_contract
CREATE INDEX ix_aiuditor_violations_tenant_contract ON public.aiuditor_violations USING btree (tenant_id, contract_id);


--

-- FK CONSTRAINT: aiuditor_admissions aiuditor_admissions_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_admissions
    ADD CONSTRAINT aiuditor_admissions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_approvals aiuditor_approvals_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_approvals
    ADD CONSTRAINT aiuditor_approvals_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_candidates aiuditor_candidates_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_candidates
    ADD CONSTRAINT aiuditor_candidates_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_catalog aiuditor_catalog_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_catalog
    ADD CONSTRAINT aiuditor_catalog_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_connections aiuditor_connections_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_connections
    ADD CONSTRAINT aiuditor_connections_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_contracts aiuditor_contracts_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_contracts
    ADD CONSTRAINT aiuditor_contracts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_envelopes aiuditor_envelopes_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_envelopes
    ADD CONSTRAINT aiuditor_envelopes_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_policies aiuditor_policies_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_policies
    ADD CONSTRAINT aiuditor_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_suite_members aiuditor_suite_members_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_suite_members
    ADD CONSTRAINT aiuditor_suite_members_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- FK CONSTRAINT: aiuditor_violations aiuditor_violations_tenant_id_fkey
ALTER TABLE ONLY public.aiuditor_violations
    ADD CONSTRAINT aiuditor_violations_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--

-- ROW SECURITY: aiuditor_admissions
ALTER TABLE public.aiuditor_admissions ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_approvals
ALTER TABLE public.aiuditor_approvals ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_candidates
ALTER TABLE public.aiuditor_candidates ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_catalog
ALTER TABLE public.aiuditor_catalog ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_connections
ALTER TABLE public.aiuditor_connections ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_contracts
ALTER TABLE public.aiuditor_contracts ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_envelopes
ALTER TABLE public.aiuditor_envelopes ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_policies
ALTER TABLE public.aiuditor_policies ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_suite_members
ALTER TABLE public.aiuditor_suite_members ENABLE ROW LEVEL SECURITY;

--

-- ROW SECURITY: aiuditor_violations
ALTER TABLE public.aiuditor_violations ENABLE ROW LEVEL SECURITY;

--

-- POLICY: aiuditor_admissions tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_admissions FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_approvals tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_approvals FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_candidates tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_candidates FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_catalog tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_catalog FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_connections tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_connections FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_contracts tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_contracts FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_envelopes tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_envelopes FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_policies tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_policies FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_suite_members tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_suite_members FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_violations tenant_isolation_delete
CREATE POLICY tenant_isolation_delete ON public.aiuditor_violations FOR DELETE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_admissions tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_admissions FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_approvals tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_approvals FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_candidates tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_candidates FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_catalog tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_catalog FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_connections tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_connections FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_contracts tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_contracts FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_envelopes tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_envelopes FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_policies tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_policies FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_suite_members tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_suite_members FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_violations tenant_isolation_insert
CREATE POLICY tenant_isolation_insert ON public.aiuditor_violations FOR INSERT WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_admissions tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_admissions FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_approvals tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_approvals FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_candidates tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_candidates FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_catalog tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_catalog FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_connections tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_connections FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_contracts tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_contracts FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_envelopes tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_envelopes FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_policies tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_policies FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_suite_members tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_suite_members FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_violations tenant_isolation_select
CREATE POLICY tenant_isolation_select ON public.aiuditor_violations FOR SELECT USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_admissions tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_admissions FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_approvals tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_approvals FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_candidates tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_candidates FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_catalog tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_catalog FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_connections tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_connections FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_contracts tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_contracts FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_envelopes tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_envelopes FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_policies tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_policies FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_suite_members tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_suite_members FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--

-- POLICY: aiuditor_violations tenant_isolation_update
CREATE POLICY tenant_isolation_update ON public.aiuditor_violations FOR UPDATE USING ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))) WITH CHECK ((((current_setting('app.current_tenant_id'::text, true) = ''::text) AND (tenant_id IS NULL)) OR ((current_setting('app.current_tenant_id'::text, true) <> ''::text) AND (tenant_id IS NOT NULL) AND (tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))));


--
