function F_x = exponential_CDF(x, lambda)
% f_x = exponential_CDF(x, lambda)
%
% It returns the CDF of a exp(lambda) r.v.
%
  F_x = (1 - exp(-lambda*x)).*(x > 0);
endfunction
