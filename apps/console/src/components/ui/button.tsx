import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-gradient-to-r from-coral via-lilac to-aqua text-ink shadow-glow hover:brightness-110 active:scale-[0.99]",
        secondary:
          "border border-white/20 bg-white/[0.06] text-white/90 backdrop-blur hover:border-white/35 hover:bg-white/10",
        ghost:
          "border border-white/15 bg-transparent text-white/70 hover:border-white/40 hover:bg-white/5 hover:text-white",
        outline:
          "border border-input bg-transparent hover:bg-accent hover:text-accent-foreground",
        destructive:
          "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-11 min-h-[44px] px-8 py-3.5",
        sm: "h-9 min-h-[36px] rounded-full px-4 text-xs",
        lg: "h-12 min-h-[48px] rounded-full px-10 text-base",
        icon: "h-11 w-11 min-h-[44px] min-w-[44px]",
        xs: "h-7 min-h-[28px] rounded-full px-2.5 text-[10px] font-bold",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
