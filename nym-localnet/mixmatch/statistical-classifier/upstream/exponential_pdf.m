function f_x = exponential_pdf(x, lambda)
% f_x = exponential_pdf(x, lambda)
%
% It returns the PDF of a exp(lambda) r.v.
%
  f_x = lambda*exp(-lambda*x).*(x > 0);
endfunction
