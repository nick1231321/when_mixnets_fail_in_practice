function y = normpdf(x, mu, sigma)
% Standard Gaussian probability density (same definition as the statistics toolbox).
% Provided so MixMatch's unmodified gaussian_delay_sequences_match_loglikelihood.m runs
% without installing octave-forge statistics.
  if nargin < 2, mu = 0; endif
  if nargin < 3, sigma = 1; endif
  y = exp(-0.5 * ((x - mu) ./ sigma) .^ 2) ./ (sigma .* sqrt(2*pi));
endfunction
