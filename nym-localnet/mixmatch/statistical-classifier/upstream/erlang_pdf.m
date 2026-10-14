function f_x = erlang_pdf(x, k, lambda)
% f_x = erlang_pdf(x, k, lambda)
%
% It returns the PDF of a Erlang(k, lambda) r.v.
%
  f_x = (lambda^k)*(x.^(k-1)).*exp(-lambda*x).*(x > 0)/factorial(k-1);
endfunction
