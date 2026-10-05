// Copyright 2026 - Nym Technologies SA <contact@nymtech.net>
// SPDX-License-Identifier: Apache-2.0

//! Path selection strategies from "When Mixnets Fail: Evaluating, Quantifying, and Mitigating
//! the Impact of Adversarial Nodes in Mix Networks" (Rahimi, NDSS 2026).
//!
//! Each strategy constrains the routes assigned to packets of the same client-destination
//! session, to reduce the chance that any packet traverses a fully compromised route.

/// How mix routes are chosen for the packets of a client-destination session.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum PathSelectionStrategy {
    /// Current Nym behaviour: every packet gets an independent, uniformly random route.
    #[default]
    Baseline,

    /// K-Hops Fixed (K-HF): the mixnodes on `fixed_layers` (1-indexed) are sampled once per
    /// session and reused by every packet; the remaining layers are sampled per packet.
    KHopsFixed { fixed_layers: Vec<usize> },

    /// K/W: `k` mixnodes are preselected per layer once per session; every packet then
    /// samples uniformly from that preselected subset in each layer.
    KOverW { k: usize },

    /// α-Sticky Selection (α-SS): with probability `alpha` a packet reuses a route already
    /// assigned in the session, otherwise it gets a fresh uniformly random route.
    AlphaSticky { alpha: f64 },
}
