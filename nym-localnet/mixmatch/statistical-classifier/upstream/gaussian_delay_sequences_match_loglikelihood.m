function loglikelihood = gaussian_delay_sequences_match_loglikelihood(Si, So, mu, sigma, return_loglikelihood_sequences)
% loglikelihood = gaussian_delay_sequences_match_loglikelihood(Si, So, mu, sigma, return_loglikelihood_sequences)
%
% It returns the maximum loglikelihood of output sequence So matching input
% sequence Si.
%
% If return_loglikelihood_sequences is not used or set to false, then only
% the total loglikelihood is returned. If it is set to true, then the sequence
% of loglikelihoods, term by term, is returned. Loglikelihood of the sequence
% match can be computed by summing the corresponding terms.
%
  assert(length(Si) == length(So));
  if nargin == 3
    return_loglikelihood_sequences = false;
  endif
  if return_loglikelihood_sequences
    loglikelihood = zeros(1, length(Si));
  else
    loglikelihood = 0;
  endif
  for k = 1:length(Si)
    L_ti_to = log(normpdf(So(k) - Si(k), mu, sigma));
    if return_loglikelihood_sequences
      loglikelihood(k) = L_ti_to;
    else
      loglikelihood = loglikelihood + L_ti_to;
    endif
    if L_ti_to == -Inf
      if return_loglikelihood_sequences
	if k < length(Si)
	  loglikelihood((k+1):end) = -Inf;
	endif
      endif
      break
    endif
  endfor
endfunction
