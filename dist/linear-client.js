/**
 * Linear API client via GraphQL.
 * Creates missing labels and workflow states automatically.
 */
import { GraphQLClient } from "graphql-request";
import { READY_LABELS, FLOW_LABELS, } from "./agent-contracts.js";
const LINEAR_API = "https://api.linear.app/graphql";
function createClient(apiKey) {
    return new GraphQLClient(LINEAR_API, {
        headers: {
            Authorization: apiKey,
            "Content-Type": "application/json",
        },
    });
}
function hasLabel(issue, labelName) {
    return (issue.labels?.nodes ?? []).some((l) => l.name === labelName);
}
function matchesFilters(issue, filters) {
    if (filters.role) {
        if (!hasLabel(issue, READY_LABELS[filters.role]))
            return false;
    }
    if (filters.withoutRole === "ba") {
        if (!hasLabel(issue, FLOW_LABELS.noBa))
            return false;
    }
    if (filters.status && issue.state?.name !== filters.status) {
        return false;
    }
    if (filters.labels?.length) {
        for (const lbl of filters.labels) {
            if (!hasLabel(issue, lbl))
                return false;
        }
    }
    return true;
}
export function createLinearClient(apiKey) {
    return createClient(apiKey);
}
/** Resolve team ID for label creation (labels belong to a team in Linear). */
export async function resolveTeamId(client, config) {
    if (config.linear.teamId)
        return config.linear.teamId;
    const query = `
    query Teams {
      teams(first: 50) {
        nodes { id key name }
      }
    }
  `;
    const data = await client.request(query);
    const teams = data.teams.nodes;
    if (teams.length === 0)
        return null;
    if (config.linear.teamKey) {
        const byKey = teams.find((t) => t.key?.toLowerCase() === config.linear.teamKey.toLowerCase());
        if (byKey)
            return byKey.id;
    }
    return teams[0].id;
}
const ISSUE_FRAGMENT = `
  id identifier title description
  state { name }
  labels { nodes { id name } }
  assignee { id name }
  priority url
`;
export async function getIssue(client, issueId) {
    const query = `
    query GetIssue($id: String!) {
      issue(id: $id) {
        ${ISSUE_FRAGMENT}
      }
    }
  `;
    const data = await client.request(query, { id: issueId });
    return data.issue ?? undefined;
}
/** Parse "ELM-62" -> { teamKey: "ELM", number: 62 } */
function parseIdentifier(identifier) {
    const m = identifier.match(/^([A-Za-z]+)-(\d+)$/);
    if (!m)
        return null;
    return { teamKey: m[1].toUpperCase(), number: parseInt(m[2], 10) };
}
export async function getIssueByIdentifier(client, identifier) {
    const parsed = parseIdentifier(identifier);
    if (!parsed) {
        const byId = await getIssue(client, identifier);
        return byId;
    }
    const query = `
    query GetIssueByIdentifier($filter: IssueFilter!) {
      issues(filter: $filter, first: 1) {
        nodes {
          ${ISSUE_FRAGMENT}
        }
      }
    }
  `;
    const filter = {
        number: { eq: parsed.number },
        team: { key: { eq: parsed.teamKey } },
    };
    const data = await client.request(query, { filter });
    return data.issues.nodes[0];
}
export async function listIssues(client, filters, limit = 50) {
    const filter = {
        state: { name: { nin: ["Done", "Canceled"] } },
    };
    if (filters.role) {
        filter.labels = { some: { name: { eq: READY_LABELS[filters.role] } } };
    }
    else if (filters.withoutRole === "ba") {
        filter.labels = { some: { name: { eq: FLOW_LABELS.noBa } } };
    }
    const query = `
    query ListIssues($filter: IssueFilter, $first: Int!) {
      issues(filter: $filter, first: $first, orderBy: updatedAt) {
        nodes {
          ${ISSUE_FRAGMENT}
        }
      }
    }
  `;
    const data = await client.request(query, {
        filter,
        first: Math.min(limit, 100),
    });
    let nodes = data.issues.nodes;
    if (filters.withoutRole === "ba" && filters.role) {
        nodes = nodes.filter((i) => hasLabel(i, FLOW_LABELS.noBa));
    }
    if (filters.status || filters.labels?.length) {
        nodes = nodes.filter((i) => matchesFilters(i, filters));
    }
    return nodes.slice(0, limit);
}
export async function getNextIssueForRole(client, role, withoutBa) {
    const readyLabel = READY_LABELS[role];
    const filter = {
        labels: { some: { name: { eq: readyLabel } } },
        state: { name: { nin: ["Done", "Canceled"] } },
    };
    const query = `
    query NextIssue($filter: IssueFilter!, $first: Int!) {
      issues(filter: $filter, first: $first, orderBy: updatedAt) {
        nodes {
          ${ISSUE_FRAGMENT}
        }
      }
    }
  `;
    const data = await client.request(query, {
        filter,
        first: withoutBa ? 50 : 10,
    });
    if (withoutBa) {
        const withNoBa = data.issues.nodes.find((i) => hasLabel(i, FLOW_LABELS.noBa));
        return withNoBa ?? data.issues.nodes[0];
    }
    return data.issues.nodes[0];
}
export async function updateIssueState(client, issueId, stateName) {
    const issueTeamQuery = `
    query IssueTeamForState($id: String!) {
      issue(id: $id) {
        id
        team { id }
      }
    }
  `;
    const issueRow = await client.request(issueTeamQuery, { id: issueId });
    const teamId = issueRow.issue?.team?.id;
    if (!teamId) {
        console.error("updateIssueState: issue has no team", issueId);
        return false;
    }
    const workflowsQuery = `
    query WorkflowStatesWithTeam {
      workflowStates(first: 250) {
        nodes { id name team { id } }
      }
    }
  `;
    const wf = await client.request(workflowsQuery);
    const state = wf.workflowStates.nodes.find((s) => s.name === stateName && s.team?.id === teamId);
    if (!state) {
        const available = wf.workflowStates.nodes
            .filter((s) => s.team?.id === teamId)
            .map((s) => s.name)
            .filter((n, i, a) => a.indexOf(n) === i);
        console.error(`updateIssueState: no state "${stateName}" for issue team. Available: ${available.join(", ") || "(none)"}`);
        return false;
    }
    const mutation = `
    mutation UpdateIssueState($id: String!, $stateId: String!) {
      issueUpdate(id: $id, input: { stateId: $stateId }) {
        success
      }
    }
  `;
    const result = await client.request(mutation, {
        id: issueId,
        stateId: state.id,
    });
    return result.issueUpdate.success;
}
export async function addLabel(client, issueId, labelName, config) {
    const labelsQuery = `
    query IssueLabels {
      issueLabels(first: 250) {
        nodes { id name }
      }
    }
  `;
    const labelsData = await client.request(labelsQuery);
    let label = labelsData.issueLabels.nodes.find((l) => l.name === labelName);
    if (!label && config) {
        const teamId = await resolveTeamId(client, config);
        if (!teamId) {
            console.error("Cannot create label: no team found. Set linear.teamId or linear.teamKey in config.");
            return false;
        }
        const createMutation = `
      mutation CreateLabel($input: IssueLabelCreateInput!) {
        issueLabelCreate(input: $input) {
          issueLabel { id name }
        }
      }
    `;
        try {
            const created = await client.request(createMutation, { input: { name: labelName, teamId } });
            label = created.issueLabelCreate.issueLabel ?? undefined;
        }
        catch (e) {
            console.error("Failed to create label:", labelName, e);
            return false;
        }
        if (!label)
            return false;
    }
    else if (!label) {
        console.error(`Label "${labelName}" not found. Add linear.teamId or linear.teamKey to config for auto-creation.`);
        return false;
    }
    const issue = await getIssue(client, issueId);
    if (!issue)
        return false;
    const currentIds = (issue.labels?.nodes ?? []).map((l) => l.id);
    if (currentIds.includes(label.id))
        return true;
    const updateMutation = `
    mutation UpdateIssueLabels($id: String!, $labelIds: [String!]!) {
      issueUpdate(id: $id, input: { labelIds: $labelIds }) {
        success
      }
    }
  `;
    const result = await client.request(updateMutation, {
        id: issueId,
        labelIds: [...currentIds, label.id],
    });
    return result.issueUpdate.success;
}
export async function removeLabel(client, issueId, labelName) {
    const issue = await getIssue(client, issueId);
    if (!issue)
        return false;
    const labels = issue.labels?.nodes ?? [];
    const toRemove = labels.find((l) => l.name === labelName);
    if (!toRemove)
        return true;
    const remaining = labels.filter((l) => l.id !== toRemove.id).map((l) => l.id);
    const mutation = `
    mutation UpdateIssueLabels($id: String!, $labelIds: [String!]!) {
      issueUpdate(id: $id, input: { labelIds: $labelIds }) {
        success
      }
    }
  `;
    const result = await client.request(mutation, {
        id: issueId,
        labelIds: remaining,
    });
    return result.issueUpdate.success;
}
export async function addComment(client, issueId, body) {
    const mutation = `
    mutation CreateComment($issueId: String!, $body: String!) {
      commentCreate(input: { issueId: $issueId, body: $body }) {
        comment { id }
      }
    }
  `;
    const result = await client.request(mutation, { issueId, body });
    return result.commentCreate.comment?.id;
}
/**
 * Assignment: we use labels (stage:, ready:) and workflow status instead of
 * Linear assignee. No direct assign — agent "ownership" is implicit from labels.
 */
export function issueToSummary(issue) {
    return {
        id: issue.id,
        identifier: issue.identifier,
        title: issue.title,
        description: issue.description ?? undefined,
        state: { name: issue.state?.name ?? "Unknown" },
        labels: {
            nodes: (issue.labels?.nodes ?? []).map((l) => ({ name: l.name })),
        },
        assignee: issue.assignee ? { name: issue.assignee.name } : undefined,
        priority: issue.priority ?? undefined,
        url: issue.url ?? undefined,
    };
}
//# sourceMappingURL=linear-client.js.map