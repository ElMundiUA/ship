/**
 * Configuration for linear-agent system.
 * Loads .env via dotenv (from CLI entry) for LINEAR_API_KEY.
 */
import { z } from "zod";
declare const ConfigSchema: z.ZodObject<{
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