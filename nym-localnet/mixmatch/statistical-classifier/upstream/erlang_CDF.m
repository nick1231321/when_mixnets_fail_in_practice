function F_x = erlang_CDF(x, k, lambda)
% f_x = erlang_CDF(x, lambda)
%
% It returns the CDF of a Erlang(k, lambda) r.v.
%
  F_x = ones(size(x)).*(x > 0);
  for n=0:(k-1)
    F_x = F_x - (x > 0).*(exp(-lambda*x).*(lambda*x).^n)/factorial(n);
  endfor
endfunction
