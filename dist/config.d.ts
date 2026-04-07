/**
 * Configuration for ship-agent (multi-tracker).
 */
import { z } from "zod";
declare const ConfigSchema: z.ZodObject<{
    tracker: z.ZodDefault<z.ZodObject<{
        provider: z.ZodDefault<z.ZodEnum<["linear", "jira", "github", "azure-devops", "clickup"]>>;
        jira: z.ZodOptional<z.ZodObject<{
            hostEnv: z.ZodOptional<z.ZodString>;
            patEnv: z.ZodOptional<z.ZodString>;
            emailEnv: z.ZodOptional<z.ZodString>;
            tokenEnv: z.ZodOptional<z.ZodString>;
        }, "strip", z.ZodTypeAny, {
            hostEnv?: string | undefined;
            patEnv?: string | undefined;
            emailEnv?: string | undefined;
            tokenEnv?: string | undefined;
        }, {
            hostEnv?: string | undefined;
            patEnv?: string | undefined;
            emailEnv?: string | undefined;
            tokenEnv?: string | undefined;
        }>>;
        github: z.ZodOptional<z.ZodObject<{
            tokenEnv: z.ZodOptional<z.ZodString>;
            altTokenEnv: z.ZodOptional<z.ZodString>;
            repoEnv: z.ZodOptional<z.ZodString>;
            ownerEnv: z.ZodOptional<z.ZodString>;
            repoNameEnv: z.ZodOptional<z.ZodString>;
        }, "strip", z.ZodTypeAny, {
            tokenEnv?: string | undefined;
            altTokenEnv?: string | undefined;
            repoEnv?: string | undefined;
            ownerEnv?: string | undefined;
            repoNameEnv?: string | undefined;
        }, {
            tokenEnv?: string | undefined;
            altTokenEnv?: string | undefined;
            repoEnv?: string | undefined;
            ownerEnv?: string | undefined;
            repoNameEnv?: string | undefined;
        }>>;
        azureDevops: z.ZodOptional<z.ZodObject<{
            orgEnv: z.ZodOptional<z.ZodString>;
            projectEnv: z.ZodOptional<z.ZodString>;
            patEnv: z.ZodOptional<z.ZodString>;
        }, "strip", z.ZodTypeAny, {
            orgEnv?: string | undefined;
            projectEnv?: string | undefined;
            patEnv?: string | undefined;
        }, {
            orgEnv?: string | undefined;
            projectEnv?: string | undefined;
            patEnv?: string | undefined;
        }>>;
        clickup: z.ZodOptional<z.ZodObject<{
            tokenEnv: z.ZodOptional<z.ZodString>;
            listIdEnv: z.ZodOptional<z.ZodString>;
            teamIdEnv: z.ZodOptional<z.ZodString>;
        }, "strip", z.ZodTypeAny, {
            tokenEnv?: string | undefined;
            listIdEnv?: string | undefined;
            teamIdEnv?: string | undefined;
        }, {
            tokenEnv?: string | undefined;
            listIdEnv?: string | undefined;
            teamIdEnv?: string | undefined;
        }>>;
    }, "strip", z.ZodTypeAny, {
        provider: "linear" | "jira" | "github" | "azure-devops" | "clickup";
        jira?: {
            hostEnv?: string | undefined;
            patEnv?: string | undefined;
            emailEnv?: string | undefined;
            tokenEnv?: string | undefined;
        } | undefined;
        github?: {
            tokenEnv?: string | undefined;
            altTokenEnv?: string | undefined;
            repoEnv?: string | undefined;
            ownerEnv?: string | undefined;
            repoNameEnv?: string | undefined;
        } | undefined;
        azureDevops?: {
            orgEnv?: string | undefined;
            projectEnv?: string | undefined;
            patEnv?: string | undefined;
        } | undefined;
        clickup?: {
            tokenEnv?: string | undefined;
            listIdEnv?: string | undefined;
            teamIdEnv?: string | undefined;
        } | undefined;
    }, {
        provider?: "linear" | "jira" | "github" | "azure-devops" | "clickup" | undefined;
        jira?: {
            hostEnv?: string | undefined;
            patEnv?: string | undefined;
            emailEnv?: string | undefined;
            tokenEnv?: string | undefined;
        } | undefined;
        github?: {
            tokenEnv?: string | undefined;
            altTokenEnv?: string | undefined;
            repoEnv?: string | undefined;
            ownerEnv?: string | undefined;
            repoNameEnv?: string | undefined;
        } | undefined;
        azureDevops?: {
            orgEnv?: string | undefined;
            projectEnv?: string | undefined;
            patEnv?: string | undefined;
        } | undefined;
        clickup?: {
            tokenEnv?: string | undefined;
            listIdEnv?: string | undefined;
            teamIdEnv?: string | undefined;
        } | undefined;
    }>>;
    linear: z.ZodObject<{
        apiKeyEnv: z.ZodDefault<z.ZodString>;
        teamId: z.ZodOptional<z.ZodString>;
        teamKey: z.ZodOptional<z.ZodString>;
        defaultProject: z.ZodOptional<z.ZodString>;
    }, "strip", z.ZodTypeAny, {
        apiKeyEnv: string;
        teamId?: string | undefined;
        teamKey?: string | undefined;
        defaultProject?: string | undefined;
    }, {
        apiKeyEnv?: string | undefined;
        teamId?: string | undefined;
        teamKey?: string | undefined;
        defaultProject?: string | undefined;
    }>;
    workflow: z.ZodObject<{
        roles: z.ZodDefault<z.ZodArray<z.ZodString, "many">>;
        skipRules: z.ZodDefault<z.ZodArray<z.ZodObject<{
            label: z.ZodString;
            skipRole: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            label: string;
            skipRole: string;
        }, {
            label: string;
            skipRole: string;
        }>, "many">>;
        labels: z.ZodObject<{
            readyPrefix: z.ZodDefault<z.ZodString>;
            stagePrefix: z.ZodDefault<z.ZodString>;
            resultPrefix: z.ZodDefault<z.ZodString>;
        }, "strip", z.ZodTypeAny, {
            readyPrefix: string;
            stagePrefix: string;
            resultPrefix: string;
        }, {
            readyPrefix?: string | undefined;
            stagePrefix?: string | undefined;
            resultPrefix?: string | undefined;
        }>;
    }, "strip", z.ZodTypeAny, {
        roles: string[];
        skipRules: {
            label: string;
            skipRole: string;
        }[];
        labels: {
            readyPrefix: string;
            stagePrefix: string;
            resultPrefix: string;
        };
    }, {
        labels: {
            readyPrefix?: string | undefined;
            stagePrefix?: string | undefined;
            resultPrefix?: string | undefined;
        };
        roles?: string[] | undefined;
        skipRules?: {
            label: string;
            skipRole: string;
        }[] | undefined;
    }>;
    release: z.ZodObject<{
        requirePreview: z.ZodDefault<z.ZodBoolean>;
        requireHumanMerge: z.ZodDefault<z.ZodBoolean>;
        requireQaPass: z.ZodDefault<z.ZodBoolean>;
    }, "strip", z.ZodTypeAny, {
        requirePreview: boolean;
        requireHumanMerge: boolean;
        requireQaPass: boolean;
    }, {
        requirePreview?: boolean | undefined;
        requireHumanMerge?: boolean | undefined;
        requireQaPass?: boolean | undefined;
    }>;
    qaBrowser: z.ZodObject<{
        provider: z.ZodDefault<z.ZodEnum<["playwright"]>>;
        headless: z.ZodDefault<z.ZodBoolean>;
        artifactsDir: z.ZodDefault<z.ZodString>;
    }, "strip", z.ZodTypeAny, {
        provider: "playwright";
        headless: boolean;
        artifactsDir: string;
    }, {
        provider?: "playwright" | undefined;
        headless?: boolean | undefined;
        artifactsDir?: string | undefined;
    }>;
}, "strip", z.ZodTypeAny, {
    tracker: {
        provider: "linear" | "jira" | "github" | "azure-devops" | "clickup";
        jira?: {
            hostEnv?: string | undefined;
            patEnv?: string | undefined;
            emailEnv?: string | undefined;
            tokenEnv?: string | undefined;
        } | undefined;
        github?: {
            tokenEnv?: string | undefined;
            altTokenEnv?: string | undefined;
            repoEnv?: string | undefined;
            ownerEnv?: string | undefined;
            repoNameEnv?: string | undefined;
        } | undefined;
        azureDevops?: {
            orgEnv?: string | undefined;
            projectEnv?: string | undefined;
            patEnv?: string | undefined;
        } | undefined;
        clickup?: {
            tokenEnv?: string | undefined;
            listIdEnv?: string | undefined;
            teamIdEnv?: string | undefined;
        } | undefined;
    };
    linear: {
        apiKeyEnv: string;
        teamId?: string | undefined;
        teamKey?: string | undefined;
        defaultProject?: string | undefined;
    };
    workflow: {
        roles: string[];
        skipRules: {
            label: string;
            skipRole: string;
        }[];
        labels: {
            readyPrefix: string;
            stagePrefix: string;
            resultPrefix: string;
        };
    };
    release: {
        requirePreview: boolean;
        requireHumanMerge: boolean;
        requireQaPass: boolean;
    };
    qaBrowser: {
        provider: "playwright";
        headless: boolean;
        artifactsDir: string;
    };
}, {
    tracker?: {
        provider?: "linear" | "jira" | "github" | "azure-devops" | "clickup" | undefined;
        jira?: {
            hostEnv?: string | undefined;
            patEnv?: string | undefined;
            emailEnv?: string | undefined;
            tokenEnv?: string | undefined;
        } | undefined;
        github?: {
            tokenEnv?: string | undefined;
            altTokenEnv?: string | undefined;
            repoEnv?: string | undefined;
            ownerEnv?: string | undefined;
            repoNameEnv?: string | undefined;
        } | undefined;
        azureDevops?: {
            orgEnv?: string | undefined;
            projectEnv?: string | undefined;
            patEnv?: string | undefined;
        } | undefined;
        clickup?: {
            tokenEnv?: string | undefined;
            listIdEnv?: string | undefined;
            teamIdEnv?: string | undefined;
        } | undefined;
    } | undefined;
    linear: {
        apiKeyEnv?: string | undefined;
        teamId?: string | undefined;
        teamKey?: string | undefined;
        defaultProject?: string | undefined;
    };
    workflow: {
        labels: {
            readyPrefix?: string | undefined;
            stagePrefix?: string | undefined;
            resultPrefix?: string | undefined;
        };
        roles?: string[] | undefined;
        skipRules?: {
            label: string;
            skipRole: string;
        }[] | undefined;
    };
    release: {
        requirePreview?: boolean | undefined;
        requireHumanMerge?: boolean | undefined;
        requireQaPass?: boolean | undefined;
    };
    qaBrowser: {
        provider?: "playwright" | undefined;
        headless?: boolean | undefined;
        artifactsDir?: string | undefined;
    };
}>;
export type Config = z.infer<typeof ConfigSchema>;
export declare function loadConfig(configPath?: string): Config;
export {};
//# sourceMappingURL=config.d.ts.map