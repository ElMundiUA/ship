"""Deploy module — provider-agnostic deploy planning + execution.

Layers (see memory ``deploy-feature-architecture``):

* ``plan``     — the provider-agnostic ``DeployPlan`` intermediate
  representation an LLM produces from repo intel.
* ``planner``  — gathers repo signals + key files and asks the model to
  emit a ``DeployPlan``.
* ``llm``      — resolves the LLM client (Ship's configured vendor, with
  a clearly-gated local-dev Gemini fallback).
* ``providers``— adapters that turn a ``DeployPlan`` into a concrete
  provider deployment (DigitalOcean App Platform is the first).

The module is a self-contained package so it can be lifted into its own
service later without untangling it from the rest of the backend.
"""
